from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Plan, SubscriptionPayment
from apps.tenants.models import Tenant


class ConsoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.starter = Plan.objects.get(slug="starter")
        cls.business = Plan.objects.get(slug="business")
        cls.admin = User.objects.create_superuser("admin@x.test", "pw")
        cls.lazz = Tenant.objects.create(name="Lazz Pharma", slug="lazz", plan=cls.business, status="active")
        cls.green = Tenant.objects.create(
            name="Green Life", slug="green", plan=cls.starter, status="trial",
            trial_ends_at=timezone.now() - timedelta(days=1),
        )
        cls.owner = User.objects.create_user("owner@lazz.test", "pw", tenant=cls.lazz, is_owner=True, first_name="Rina")
        User.objects.create_user("owner@green.test", "pw", tenant=cls.green, is_owner=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def _data(self, **params):
        base = {"draw": 1, "start": 0, "length": 25}
        base.update(params)
        return self.client.get(reverse("platform:tenant_data"), base).json()

    def _action(self, tenant, action, data=None):
        return self.client.post(reverse("platform:tenant_action", args=[tenant.pk, action]), data or {}, follow=True)

    # Access
    def test_pharmacy_staff_cannot_open_console(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("platform:tenants")).status_code, 403)
        self.assertEqual(self.client.get(reverse("platform:tenant_data")).status_code, 403)
        self.assertEqual(self._action(self.green, "reactivate").status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("platform:tenants"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('platform:tenants')}")

    # List
    def test_list_data_search_and_filters(self):
        self.assertEqual(self.client.get(reverse("platform:tenants")).status_code, 200)
        data = self._data()
        self.assertEqual((data["recordsTotal"], len(data["data"])), (2, 2))
        self.assertEqual(self._data(**{"search[value]": "owner@lazz"})["recordsFiltered"], 1)
        expired = self._data(state="expired")
        self.assertEqual(expired["recordsTotal"], 1)
        self.assertIn("Green Life", expired["data"][0][0])
        self.assertEqual(self._data(plan=self.business.pk)["recordsTotal"], 1)
        ordered = self._data(**{"order[0][column]": 0, "order[0][dir]": "asc"})
        self.assertIn("Green Life", ordered["data"][0][0])

    def test_list_rows_escape_pharmacy_names(self):
        Tenant.objects.create(name="<script>x</script>", slug="evil", status="active")
        rows = self._data(**{"search[value]": "evil"})["data"]
        self.assertIn("&lt;script&gt;", rows[0][0])

    # Detail & actions
    def test_detail_page(self):
        response = self.client.get(reverse("platform:tenant_detail", args=[self.lazz.pk]))
        self.assertContains(response, "Lazz Pharma")
        self.assertContains(response, "Record payment")

    def test_record_payment(self):
        response = self._action(self.green, "payment", {"plan": self.business.pk, "months": 3, "method": "bkash", "reference": "TX1"})
        self.assertContains(response, "Payment recorded.")
        payment = SubscriptionPayment.objects.get()
        self.assertEqual((payment.tenant, payment.amount, payment.recorded_by), (self.green, 7500, self.admin))
        self.green.refresh_from_db()
        self.assertTrue(self.green.has_access)

    def test_invalid_payment_shows_error(self):
        response = self._action(self.green, "payment", {"plan": self.business.pk, "months": 5, "method": "cash"})
        self.assertContains(response, "Period:")
        self.assertFalse(SubscriptionPayment.objects.exists())

    def test_change_plan_refusal_is_shown(self):
        for i in range(3):
            User.objects.create_user(f"s{i}@lazz.test", "pw", tenant=self.lazz)
        response = self._action(self.lazz, "plan", {"plan": self.starter.pk})
        self.assertContains(response, "uses more than Starter allows")
        self.lazz.refresh_from_db()
        self.assertEqual(self.lazz.plan, self.business)

    def test_suspend_then_reactivate(self):
        self.assertContains(self._action(self.lazz, "suspend", {"reason": "Cheque bounced"}), "Pharmacy suspended.")
        self.lazz.refresh_from_db()
        self.assertEqual(self.lazz.status, "suspended")
        self.assertContains(self._action(self.lazz, "reactivate"), "Pharmacy reactivated.")
        self.lazz.refresh_from_db()
        self.assertEqual(self.lazz.status, "active")  # complimentary account (no dates) stays active

    def test_extend_trial(self):
        self._action(self.green, "trial", {"days": 10})
        self.green.refresh_from_db()
        self.assertTrue(self.green.has_access)

    def test_unknown_action_and_get_are_rejected(self):
        self.assertEqual(self._action(self.lazz, "delete").status_code, 404)
        url = reverse("platform:tenant_action", args=[self.lazz.pk, "suspend"])
        self.assertEqual(self.client.get(url).status_code, 405)

    # Add pharmacy
    def _new_pharmacy(self, **overrides):
        data = {
            "business_name": "Shifa Pharmacy", "business_phone": "01555000000", "address": "Mirpur",
            "owner_name": "Karim Uddin", "email": "karim@shifa.test", "phone": "01555000001",
            "password1": "Strong#Pass2026", "password2": "Strong#Pass2026",
            "plan": self.business.pk, "trial_days": 30,
        }
        data.update(overrides)
        return self.client.post(reverse("platform:tenant_create"), data)

    def test_admin_adds_pharmacy_with_owner_and_outlet(self):
        self.assertContains(self.client.get(reverse("platform:tenants")), reverse("platform:tenant_create"))
        before = timezone.now()
        response = self._new_pharmacy()
        tenant = Tenant.objects.get(slug="shifa-pharmacy")
        self.assertRedirects(response, reverse("platform:tenant_detail", args=[tenant.pk]))
        self.assertEqual((tenant.plan, tenant.status), (self.business, "trial"))
        self.assertGreaterEqual(tenant.trial_ends_at, before + timedelta(days=30))
        owner = User.objects.get(email="karim@shifa.test")
        self.assertTrue(owner.is_owner and owner.tenant == tenant and owner.check_password("Strong#Pass2026"))
        self.assertTrue(tenant.events.get().message.startswith("Added by ThingBD"))
        self.assertEqual(tenant.events.get().actor, self.admin)
        # The admin stays logged in as themself.
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin.pk)

    def test_admin_can_use_hidden_plans(self):
        hidden = Plan.objects.create(name="Partner", slug="partner", is_public=False)
        self._new_pharmacy(plan=hidden.pk)
        self.assertEqual(Tenant.objects.get(slug="shifa-pharmacy").plan, hidden)

    def test_add_pharmacy_validates_like_signup(self):
        response = self._new_pharmacy(email="owner@lazz.test")
        self.assertContains(response, "An account with this email already exists.")
        self.assertFalse(Tenant.objects.filter(slug="shifa-pharmacy").exists())

    def test_pharmacy_staff_cannot_add_pharmacies(self):
        self.client.force_login(self.owner)
        self.assertEqual(self._new_pharmacy().status_code, 403)
        self.assertFalse(Tenant.objects.filter(slug="shifa-pharmacy").exists())

    # Plans
    def test_create_and_edit_plan(self):
        self.assertContains(self.client.get(reverse("platform:plans")), "Starter")
        response = self.client.post(reverse("platform:plan_create"), {
            "name": "Clinic", "slug": "clinic", "description": "", "price_monthly": "1500", "price_yearly": "",
            "max_outlets": "2", "max_users": "", "max_medicines": "", "sort_order": "4", "is_public": "on", "is_active": "on",
        })
        self.assertRedirects(response, reverse("platform:plans"))
        clinic = Plan.objects.get(slug="clinic")
        self.assertIsNone(clinic.max_users)
        self.client.post(reverse("platform:plan_edit", args=[clinic.pk]), {
            "name": "Clinic", "slug": "clinic", "description": "", "price_monthly": "1800", "price_yearly": "",
            "max_outlets": "2", "max_users": "", "max_medicines": "", "sort_order": "4", "is_active": "on",
        })
        clinic.refresh_from_db()
        self.assertEqual((clinic.price_monthly, clinic.is_public), (1800, False))
