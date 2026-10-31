from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.outlets.models import Outlet

from .models import Tenant, TenantEvent

SIGNUP = reverse("tenants:signup")


@override_settings(TRIAL_DAYS=14)
class SignupTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.get(slug="business")

    def _data(self, **overrides):
        data = {
            "business_name": "Lazz Pharma", "business_phone": "01710000000", "address": "Dhanmondi, Dhaka",
            "owner_name": "Rina Akter", "email": "Rina@Lazz.test", "phone": "01710000001",
            "password1": "Strong#Pass2026", "password2": "Strong#Pass2026",
            "plan": self.plan.pk, "website": "",
        }
        data.update(overrides)
        return data

    def test_signup_page_offers_only_public_active_plans(self):
        Plan.objects.create(name="Secret", slug="secret", is_public=False)
        response = self.client.get(SIGNUP)
        names = [p.name for p in response.context["plans"]]
        self.assertEqual(names, ["Starter", "Business", "Enterprise"])

    def test_signup_creates_pharmacy_owner_and_outlet(self):
        before = timezone.now()
        response = self.client.post(SIGNUP, self._data())
        self.assertRedirects(response, reverse("core:dashboard"))

        tenant = Tenant.objects.get()
        self.assertEqual((tenant.name, tenant.slug, tenant.plan, tenant.status), ("Lazz Pharma", "lazz-pharma", self.plan, "trial"))
        self.assertGreaterEqual(tenant.trial_ends_at, before + timedelta(days=14))
        owner = User.objects.get()
        self.assertEqual((owner.email, owner.first_name, owner.last_name), ("rina@lazz.test", "Rina", "Akter"))
        self.assertTrue(owner.is_owner)
        self.assertEqual(owner.tenant, tenant)
        self.assertEqual(int(self.client.session["_auth_user_id"]), owner.pk)
        outlet = Outlet.all_objects.get()
        self.assertEqual((outlet.tenant, outlet.name, outlet.code), (tenant, "Main branch", "0001"))
        self.assertEqual(tenant.events.get().kind, TenantEvent.Kind.SIGNUP)

    def test_duplicate_business_name_gets_unique_slug(self):
        self.client.post(SIGNUP, self._data())
        self.client.logout()
        self.client.post(SIGNUP, self._data(email="b@lazz.test", phone="01710000009"))
        self.assertEqual(sorted(Tenant.objects.values_list("slug", flat=True)), ["lazz-pharma", "lazz-pharma-2"])

    def test_existing_email_or_phone_is_rejected(self):
        User.objects.create_user("rina@lazz.test", "pw", phone="01710000001")
        response = self.client.post(SIGNUP, self._data())
        self.assertContains(response, "An account with this email already exists.")
        self.assertContains(response, "Another account already uses this phone number.")
        self.assertFalse(Tenant.objects.exists())

    def test_weak_or_mismatched_password_is_rejected(self):
        self.assertContains(self.client.post(SIGNUP, self._data(password1="12345678", password2="12345678")), "too common")
        self.assertContains(self.client.post(SIGNUP, self._data(password2="Other#Pass2026")), "don&#x27;t match")
        self.assertFalse(Tenant.objects.exists())

    def test_honeypot_blocks_bots(self):
        self.client.post(SIGNUP, self._data(website="http://spam.example"))
        self.assertFalse(Tenant.objects.exists())

    def test_logged_in_user_is_sent_to_dashboard(self):
        self.client.force_login(User.objects.create_user("x@x.test", "pw"))
        self.assertRedirects(self.client.get(SIGNUP), reverse("core:dashboard"), fetch_redirect_response=False)
