"""Answering staff questions: from the built-in help, or from an AI behind any OpenAI-compatible chat API
(Groq, OpenRouter, Gemini's OpenAI endpoint, a local Ollama, OpenAI…), set by the platform admin."""
import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from . import help as helpdesk
from .models import ChatMessage, ChatThread, SupportSettings

TIMEOUT = 40  # seconds
HISTORY = 10  # earlier messages sent along for context
MAX_QUESTION = 1000
MIN_GAP = timedelta(seconds=2)


class AssistantError(Exception):
    """A user-facing reason the question wasn't taken."""


class ProviderError(Exception):
    pass


def chat_completion(config, messages):
    """POST {base_url}/chat/completions. Returns (text, tokens used) or raises ProviderError."""
    body = {"model": config.model, "messages": messages, "temperature": float(config.temperature), "max_tokens": config.max_tokens}
    headers = {"Content-Type": "application/json", "User-Agent": "MediPOS"}
    if config.key:
        headers["Authorization"] = f"Bearer {config.key}"
    request = urllib.request.Request(config.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ProviderError(f"HTTP {error.code}: {error.read(300).decode('utf-8', 'replace')}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise ProviderError(str(getattr(error, "reason", error))[:300])
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise ProviderError(str(data)[:300])
    return text.strip(), int((data.get("usage") or {}).get("total_tokens") or 0)


def system_prompt(config, tenant, page, question):
    articles = helpdesk.search(question, limit=6) or helpdesk.ARTICLES[:6]
    reference = "\n\n".join(f"## {a.title}\n{a.body}" + (f"\nPage: {helpdesk.url_of(a)}" if helpdesk.url_of(a) else "") for a in articles)
    contact = config.contact or "ThingBD support"
    return (
        "You are the support assistant inside ThingBD MediPOS, the pharmacy point-of-sale, stock and accounts software "
        f"used by \"{tenant.name}\" in Bangladesh. Help their staff use MediPOS.\n"
        "- Answer only questions about using MediPOS. For anything else, say politely that you can only help with MediPOS.\n"
        "- Base answers on the help below and don't invent features or menus. If it isn't covered, say so and suggest "
        f"contacting {contact}.\n"
        "- Reply in the language the person writes in (Bangla or English). Be short: a few steps with menu paths like "
        "\"Sales → Sale returns\". You may link a page as [name](/path) using the paths given below.\n"
        "- Never ask for passwords, card numbers or patients' and customers' personal details.\n"
        f"- They are on the page {page or '/'} and their screen language is {get_language() or 'en'}.\n"
        + (f"\n{config.instructions.strip()}\n" if config.instructions.strip() else "")
        + "\nHelp articles:\n\n" + reference
    )


def builtin_answer(question, config):
    found = helpdesk.search(question)
    if not found:
        text = _("I couldn't find that in the help.")
        if config.contact:
            text += " " + _("A person can help: %(contact)s") % {"contact": config.contact}
        return text
    return "\n\n".join(helpdesk.as_text(article) for article in found[:2])


def ai_answers_today(tenant):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return ChatMessage.all_objects.filter(tenant=tenant, role=ChatMessage.Role.ASSISTANT, source=ChatMessage.Source.AI,
                                          created_at__gte=start).count()


def ask(*, tenant, user, question, thread=None, page=""):
    """Store the question, answer it, store the answer. Returns (thread, answer message)."""
    config = SupportSettings.load()
    question = (question or "").strip()
    if not question:
        raise AssistantError(_("Type a question."))
    if len(question) > MAX_QUESTION:
        raise AssistantError(_("Please keep it under %(n)s characters.") % {"n": MAX_QUESTION})
    last = ChatMessage.objects.filter(thread__user=user, role=ChatMessage.Role.USER).order_by("-created_at").first()
    if last and timezone.now() - last.created_at < MIN_GAP:
        raise AssistantError(_("One moment: wait for the answer before asking again."))

    with transaction.atomic():
        if thread is None:
            thread = ChatThread.objects.create(user=user, title=question[:120])
        ChatMessage.objects.create(thread=thread, role=ChatMessage.Role.USER, content=question, page=page[:200])
        history = list(thread.messages.exclude(source=ChatMessage.Source.ERROR).order_by("-created_at", "-id")[1:HISTORY + 1])

    source, tokens, note = ChatMessage.Source.HELP, 0, ""
    if config.uses_ai and ai_answers_today(tenant) < config.daily_limit:
        messages = [{"role": "system", "content": system_prompt(config, tenant, page, question)}]
        messages += [{"role": m.role, "content": m.content} for m in reversed(history)]
        messages.append({"role": "user", "content": question})
        try:
            text, tokens = chat_completion(config, messages)
            source = ChatMessage.Source.AI
        except ProviderError:
            text, note = builtin_answer(question, config), _("The AI didn't answer, so here is the built-in help.")
    else:
        text = builtin_answer(question, config)
        if config.uses_ai:
            note = _("Today's AI answers are used up, so here is the built-in help.")
    if note:
        text = f"_{note}_\n\n{text}"
    answer = ChatMessage.objects.create(thread=thread, role=ChatMessage.Role.ASSISTANT, content=text or _("I couldn't find that in the help."),
                                        source=source, tokens=tokens)
    thread.save(update_fields=["updated_at"])
    return thread, answer


def test_connection(config):
    """(ok, text) for the console's "Test" button."""
    if not config.uses_ai:
        return False, _("Choose the AI engine and fill in the base URL and model first.")
    try:
        text, _tokens = chat_completion(config, [{"role": "user", "content": "Reply with: MediPOS assistant is working."}])
    except ProviderError as error:
        return False, str(error)
    return True, text
