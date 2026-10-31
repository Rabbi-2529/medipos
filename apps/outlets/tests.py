from django.test import TestCase
from django.urls import reverse

from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_pharmacy

from .middleware import SESSION_KEY
from .models import Counter, Outlet


class OutletTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()  # Business: 3 outlets
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()

    def _create(self, **data):
        payload = {"name": "Gulshan", "code": "0002", "phone": "", "email": "", "address": ""}
        payload.update(data)
        return self.client.post(reverse("outlets:create"), payload)

    def test_create_suggests_next_code_and_saves(self):
        self.assertEqual(self.client.get(reverse("outlets:create")).context["form"].initial["code"], "0002")
        self.assertRedirects(self._create(), reverse("outlets:list"))
        with tenant_context(self.tenant):
            self.assertTrue(Outlet.objects.filter(name="Gulshan", code="0002").exists())

    def test_duplicate_name_or_code_is_a_form_error(self):
        response = self._create(name="Main branch", code="0001")
        self.assertContains(response, "already exists")

    def test_plan_outlet_limit(self):
        tenant, owner = make_pharmacy("Small Shop", plan="starter")  # 1 outlet
        self.client.force_login(owner)
        response = self._create()
        self.assertContains(response, "plan allows 1 outlets")

    def test_other_pharmacys_outlet_is_404(self):
        other, _ = make_pharmacy("Green Life")
        foreign = Outlet.all_objects.get(tenant=other)
        self.assertEqual(self.client.get(reverse("outlets:edit", args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("outlets:toggle", args=[foreign.pk])).status_code, 404)

    def test_last_active_outlet_cannot_be_deactivated(self):
        response = self.client.post(reverse("outlets:toggle", args=[self.main.pk]), follow=True)
        self.assertContains(response, "only active outlet")
        self._create()
        self.client.post(reverse("outlets:toggle", args=[self.main.pk]))
        self.main.refresh_from_db()
        self.assertFalse(self.main.is_active)


class CurrentOutletTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()
            self.second = Outlet.objects.create(name="Gulshan", code="0002")

    def test_defaults_to_first_allowed_and_switches(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["current_outlet"], self.main)
        self.client.post(reverse("outlets:enter", args=[self.second.pk]), {"next": "/"})
        self.assertEqual(self.client.session[SESSION_KEY], self.second.pk)
        self.assertEqual(self.client.get(reverse("core:dashboard")).context["current_outlet"], self.second)

    def test_staff_only_reach_assigned_outlets(self):
        staff = add_staff(self.tenant, "s@x.test", [], outlets=[self.second])
        self.client.force_login(staff)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["current_outlet"], self.second)
        self.assertEqual(response.context["allowed_outlets"], [self.second])
        self.assertEqual(self.client.post(reverse("outlets:enter", args=[self.main.pk])).status_code, 404)

    def test_unsafe_next_is_ignored(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("outlets:enter", args=[self.second.pk]), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("core:dashboard"))


class CounterTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()

    def test_create_counter(self):
        response = self.client.post(reverse("outlets:counter_create"), {"outlet": self.main.pk, "name": "Counter 1", "description": ""})
        self.assertRedirects(response, reverse("outlets:counters"))
        with tenant_context(self.tenant):
            self.assertEqual(Counter.objects.get().outlet, self.main)

    def test_outlet_choices_are_own_pharmacy_only(self):
        other, _ = make_pharmacy("Green Life")
        foreign = Outlet.all_objects.get(tenant=other)
        response = self.client.post(reverse("outlets:counter_create"), {"outlet": foreign.pk, "name": "Sneaky", "description": ""})
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(Counter.all_objects.exists())
