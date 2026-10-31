import os
from unittest import mock

from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.menu import build_menu, flatten_menu
from apps.tenants.models import Tenant


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Lazz Pharma", slug="lazz", status=Tenant.Status.ACTIVE)
        cls.owner = User.objects.create_user("owner@x.test", "pw", tenant=cls.tenant, first_name="Rina")
        cls.admin = User.objects.create_superuser("admin@x.test", "pw")

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/")

    def test_pharmacy_user_sees_their_business(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "Welcome, Rina!")
        self.assertContains(response, "Lazz Pharma")
        self.assertNotContains(response, "Platform admin")
        self.assertNotIn("tenant_counts", response.context)

    def test_platform_admin_sees_tenant_counts(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["tenant_counts"]["total"], 1)
        self.assertEqual(response.context["tenant_counts"]["active"], 1)

    def test_healthz(self):
        self.assertEqual(self.client.get(reverse("core:healthz")).content, b"ok")


class MenuTests(TestCase):
    def _menu_for(self, user, path="/"):
        request = RequestFactory().get(path)
        request.user = user
        request.resolver_match = None
        return build_menu(request)

    def test_unbuilt_pages_are_hidden(self):
        tenant = Tenant.objects.create(name="T", slug="t")
        user = User.objects.create_user("u@x.test", "pw", tenant=tenant)
        labels = [e["label"] for e in flatten_menu(self._menu_for(user))]
        # Only the dashboard exists in Phase 0; every later menu stays hidden until its URL exists.
        self.assertEqual(labels, ["Dashboard"])

    def test_tenant_users_never_get_platform_section(self):
        tenant = Tenant.objects.create(name="T", slug="t")
        user = User.objects.create_user("u@x.test", "pw", tenant=tenant)
        titles = [str(s["title"]) for s in self._menu_for(user)]
        self.assertNotIn("Platform", titles)


class SeedDemoTests(TestCase):
    def test_seed_is_idempotent(self):
        with mock.patch.dict(os.environ, {"DEMO_PASSWORD": "Seed#Pass2026"}):
            call_command("seed_demo", stdout=open(os.devnull, "w"))
            call_command("seed_demo", stdout=open(os.devnull, "w"))
        self.assertEqual(Tenant.objects.count(), 2)
        self.assertEqual(User.objects.count(), 3)
        owner = User.objects.get(email="owner@lazzpharma.local")
        self.assertEqual(owner.tenant.slug, "lazz-pharma-demo")
        self.assertTrue(owner.check_password("Seed#Pass2026"))
        self.assertTrue(User.objects.get(email="admin@medipos.local").is_platform_admin)
