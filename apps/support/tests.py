import json
import urllib.error
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_pharmacy

from .models import ChatMessage, SupportSettings


def ai_reply(text, tokens=42):
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"total_tokens": tokens}}).encode()
    return response


class AssistantTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def turn_on(self, **fields):
        config = SupportSettings.load()
        config.enabled = True
        for name, value in fields.items():
            setattr(config, name, value)
        config.save()
        return config

    def ask(self, text, thread=None):
        ChatMessage.all_objects.update(created_at=timezone.now() - timedelta(minutes=1))  # past the "one moment" gap
        return self.client.post(reverse("support:send"), json.dumps({"message": text, "thread": thread, "page": "/sales/"}),
                                content_type="application/json")

    def test_only_when_switched_on_and_allowed(self):
        self.assertNotContains(self.client.get(reverse("core:dashboard")), "support-panel")
        self.assertEqual(self.client.get(reverse("support:chat")).status_code, 404)
        self.turn_on()
        self.assertContains(self.client.get(reverse("core:dashboard")), "support-panel")  # the owner has every permission
        clerk = add_staff(self.tenant, "c@x.test", ["pos.create"], all_outlets=True)
        self.client.force_login(clerk)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), "support-panel")
        self.assertEqual(self.client.get(reverse("support:chat")).status_code, 403)
        self.client.force_login(add_staff(self.tenant, "h@x.test", ["support.list"]))
        self.assertContains(self.client.get(reverse("core:dashboard")), "support-panel")

    def test_built_in_help_answers_for_free(self):
        self.turn_on(contact="WhatsApp 01700000000")
        answer = self.ask("How do I return a sale?").json()
        self.assertEqual(answer["answer"]["source"], "help")
        self.assertIn("Taking back a sale", answer["answer"]["content"])
        self.assertIn("WhatsApp 01700000000", self.ask("What's the weather tomorrow?", answer["thread"]).json()["answer"]["content"])
        state = self.client.get(reverse("support:chat")).json()
        self.assertEqual((state["thread"], len(state["messages"])), (answer["thread"], 4))

    def test_ai_answers_with_the_help_and_the_conversation(self):
        self.turn_on(engine="ai", base_url="https://ai.example/v1", model="some-model", api_key="KEY")
        with mock.patch("urllib.request.urlopen", return_value=ai_reply("Go to Sales → Sale list and press Return.")) as urlopen:
            first = self.ask("How do I return a sale?").json()
            self.ask("And refund cash?", first["thread"])
        self.assertEqual(first["answer"]["source"], "ai")
        request = urlopen.call_args_list[1].args[0]
        body = json.loads(request.data)
        self.assertEqual((request.full_url, request.get_header("Authorization"), body["model"]),
                         ("https://ai.example/v1/chat/completions", "Bearer KEY", "some-model"))
        self.assertIn(self.tenant.name, body["messages"][0]["content"])
        self.assertIn("Taking back a sale", body["messages"][0]["content"])
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        with tenant_context(self.tenant):
            self.assertEqual(ChatMessage.objects.filter(source="ai").first().tokens, 42)

    def test_falls_back_to_the_help(self):
        self.turn_on(engine="ai", base_url="https://ai.example/v1", model="m", daily_limit=1)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            failed = self.ask("How do I return a sale?").json()["answer"]
        self.assertEqual(failed["source"], "help")
        self.assertIn("The AI didn't answer", failed["content"])
        with mock.patch("urllib.request.urlopen", return_value=ai_reply("ok")):
            self.assertEqual(self.ask("How do I sell?").json()["answer"]["source"], "ai")
            limited = self.ask("How do I sell?").json()["answer"]
        self.assertIn("used up", limited["content"])

    def test_one_question_at_a_time_and_own_threads_only(self):
        self.turn_on()
        self.ask("How do I sell?")
        too_soon = self.client.post(reverse("support:send"), json.dumps({"message": "Again"}), content_type="application/json")
        self.assertEqual(too_soon.status_code, 400)
        other = add_staff(self.tenant, "o@x.test", ["support.list"])
        with tenant_context(self.tenant):
            mine = ChatMessage.objects.first().thread_id
        self.client.force_login(other)
        answer = self.ask("How do I sell?", mine).json()
        self.assertNotEqual(answer["thread"], mine)  # someone else's conversation isn't continued

    def test_platform_console(self):
        admin = User.objects.create_superuser("root@x.test", "pw")
        self.client.force_login(admin)
        url = reverse("platform:support")
        self.assertContains(self.client.get(url), "Groq")
        response = self.client.post(url, {"enabled": "on", "engine": "ai", "base_url": "", "model": "", "temperature": "0.3",
                                          "max_tokens": 600, "daily_limit": 100})
        self.assertContains(response, "needs the API base URL and the model")
        self.client.post(url, {"enabled": "on", "engine": "ai", "base_url": "https://ai.example/v1/chat/completions", "model": "m",
                               "temperature": "0.3", "max_tokens": 600, "daily_limit": 100, "contact": "Call us"})
        config = SupportSettings.load()
        self.assertEqual((config.enabled, config.base_url, config.updated_by), (True, "https://ai.example/v1", admin))
        with mock.patch("urllib.request.urlopen", return_value=ai_reply("MediPOS assistant is working.")):
            self.assertContains(self.client.post(url, {"action": "test"}, follow=True), "MediPOS assistant is working.")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
