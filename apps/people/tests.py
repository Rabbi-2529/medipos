from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.menu import MENU, build_menu, flatten_menu
from apps.core.permissions import ALL_CODES, MODULES_BY_KEY
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import PASSWORD, add_staff, make_pharmacy
from apps.outlets.models import Outlet

from .access import allowed_outlets, user_permissions
from .models import Employee, Role


def staff_form(**overrides):
    data = {
        "first_name": "Rafi", "last_name": "Hasan", "email": "rafi@lazz.test", "phone": "",
        "password1": PASSWORD, "password2": PASSWORD, "role": "", "designation": "Pharmacist",
        "all_outlets": "on", "joined_on": "", "nid": "", "address": "",
    }
    data.update(overrides)
    return data


class PermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.owner = make_pharmacy()

    def test_owner_has_everything(self):
        self.assertEqual(user_permissions(self.owner), ALL_CODES)

    def test_staff_get_their_roles_codes_and_future_codes_are_ignored(self):
        user = add_staff(self.tenant, "a@x.test", ["outlet.list", "expense.create"])  # expenses arrive in Phase 7
        self.assertEqual(user_permissions(user), {"outlet.list"})

    def test_no_role_or_platform_admin_means_nothing(self):
        self.assertEqual(user_permissions(add_staff(self.tenant, "b@x.test")), frozenset())
        self.assertEqual(user_permissions(User.objects.create_superuser("root@x.test", "pw")), frozenset())

    def test_default_roles(self):
        with tenant_context(self.tenant):
            manager = Role.objects.get(template_key="manager")
            cashier = Role.objects.get(template_key="cashier")
        self.assertIn("outlet.create", manager.codes)
        self.assertNotIn("role.create", manager.codes)
        self.assertNotIn("subscription.show", manager.codes)
        # Stored codes switch on as their modules arrive.
        self.assertEqual(cashier.codes, {
            "item.list", "stock.list", "pos.create", "sale.list", "sale_return.list", "sale_return.create",
            "customer.list", "customer.create", "customer_receive.create", "quotation.list", "quotation.create",
        })
        self.assertIn("pos.list", cashier.permissions)  # not a real action: kept but ignored
        self.assertIn("purchase.create", manager.codes)
        self.assertIn("register.list", manager.codes)
        self.assertIn("item_setup.create", manager.codes)

    def test_menu_follows_permissions(self):
        user = add_staff(self.tenant, "c@x.test", ["outlet.list"])
        request = type("R", (), {"user": user, "tenant": self.tenant, "resolver_match": None})()
        labels = [e["label"] for e in flatten_menu(build_menu(request))]
        self.assertEqual(labels, ["Dashboard", "Outlet list"])

    def test_every_tenant_menu_entry_is_a_registered_module(self):
        def walk(items):
            for item in items:
                yield item
                yield from walk(item.children)
        keys = {i.key for s in MENU for i in walk(s.items) if i.audience == "tenant" and i.url_name}
        built = {k for k in keys if k in {"settings", "outlet", "counter", "subscription", "payment_account", "employee",
                                          "role", "item", "item_setup", "stock", "purchase", "purchase_return",
                                          "supplier", "supplier_payment"}}
        self.assertTrue(built <= set(MODULES_BY_KEY))


class ShopAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.owner = make_pharmacy()

    def test_permission_is_required(self):
        self.client.force_login(add_staff(self.tenant, "a@x.test", ["counter.list"]))
        self.assertEqual(self.client.get(reverse("outlets:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("outlets:counters")).status_code, 200)

    def test_platform_admin_cannot_use_shop_screens(self):
        self.client.force_login(User.objects.create_superuser("root@x.test", "pw"))
        self.assertEqual(self.client.get(reverse("outlets:list")).status_code, 403)

    def test_allowed_outlets(self):
        with tenant_context(self.tenant):
            main = Outlet.objects.get()
            second = Outlet.objects.create(name="Gulshan", code="0002")
            Outlet.objects.create(name="Closed", code="0003", is_active=False)
        self.assertEqual(list(allowed_outlets(self.owner)), [main, second])
        only_second = add_staff(self.tenant, "a@x.test", [], outlets=[second])
        self.assertEqual(list(allowed_outlets(only_second)), [second])
        everywhere = add_staff(self.tenant, "b@x.test", [], all_outlets=True)
        self.assertEqual(list(allowed_outlets(everywhere)), [main, second])


class EmployeeTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy(plan="starter")  # 3 users max
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.manager = Role.objects.get(template_key="manager")
            self.outlet = Outlet.objects.get()

    def test_create_employee(self):
        response = self.client.post(reverse("people:employee_create"), staff_form(
            role=self.manager.pk, all_outlets="", outlets=[self.outlet.pk], phone="01711111111",
        ))
        self.assertRedirects(response, reverse("people:employees"))
        user = User.objects.get(email="rafi@lazz.test")
        self.assertEqual(user.tenant, self.tenant)
        self.assertTrue(user.check_password(PASSWORD))
        employee = user.employee
        with tenant_context(self.tenant):  # related managers are tenant-scoped too
            outlets = list(employee.outlets.all())
        self.assertEqual((employee.role, employee.designation, outlets), (self.manager, "Pharmacist", [self.outlet]))
        self.assertIn("outlet.create", user_permissions(user))

    def test_outlets_required_unless_all(self):
        response = self.client.post(reverse("people:employee_create"), staff_form(all_outlets=""))
        self.assertContains(response, "Pick at least one outlet")

    def test_plan_user_limit(self):
        for email in ("a@lazz.test", "b@lazz.test"):
            self.client.post(reverse("people:employee_create"), staff_form(email=email))
        response = self.client.post(reverse("people:employee_create"), staff_form(email="c@lazz.test"))
        self.assertContains(response, "plan allows 3 users")
        self.assertFalse(User.objects.filter(email="c@lazz.test").exists())

    def test_role_of_another_pharmacy_is_rejected(self):
        other, _ = make_pharmacy("Green Life")
        with tenant_context(other):
            foreign = Role.objects.get(template_key="manager")
        response = self.client.post(reverse("people:employee_create"), staff_form(role=foreign.pk))
        self.assertContains(response, "Select a valid choice")

    def test_edit_deactivate_and_reactivate(self):
        user = add_staff(self.tenant, "s@lazz.test", [], all_outlets=True)
        employee = user.employee
        response = self.client.post(reverse("people:employee_edit", args=[employee.pk]), staff_form(
            email="s@lazz.test", first_name="Sumi", role=self.manager.pk,
        ))
        self.assertRedirects(response, reverse("people:employees"))
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Sumi")

        self.client.post(reverse("people:employee_toggle", args=[employee.pk]))
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        add_staff(self.tenant, "fill1@lazz.test", [])
        add_staff(self.tenant, "fill2@lazz.test", [])  # plan now full again
        response = self.client.post(reverse("people:employee_toggle", args=[employee.pk]), follow=True)
        self.assertContains(response, "plan allows 3 users")

    def test_owner_and_self_are_protected(self):
        manager = add_staff(self.tenant, "m@lazz.test", ["employee.list", "employee.edit", "employee.delete"], all_outlets=True)
        self.client.force_login(manager)
        owner_profile = self.owner.employee
        response = self.client.post(reverse("people:employee_toggle", args=[owner_profile.pk]), follow=True)
        self.assertContains(response, "can&#x27;t be deactivated")
        response = self.client.post(reverse("people:employee_toggle", args=[manager.employee.pk]), follow=True)
        self.assertContains(response, "your own account")
        response = self.client.post(reverse("people:employee_edit", args=[owner_profile.pk]), staff_form(email=self.owner.email, first_name="Hacked"), follow=True)
        self.assertContains(response, "Only the owner can change")
        self.owner.refresh_from_db()
        self.assertNotEqual(self.owner.first_name, "Hacked")

    def test_reset_password(self):
        user = add_staff(self.tenant, "s@lazz.test", [], all_outlets=True)
        self.client.post(reverse("people:employee_password", args=[user.employee.pk]), {"password1": "New#Pass2026x", "password2": "New#Pass2026x"})
        user.refresh_from_db()
        self.assertTrue(user.check_password("New#Pass2026x"))

    def test_list_data_is_scoped_to_pharmacy(self):
        other, _ = make_pharmacy("Green Life")
        add_staff(other, "outsider@green.test", [])
        add_staff(self.tenant, "insider@lazz.test", [])
        data = self.client.get(reverse("people:employee_data"), {"draw": 1, "start": 0, "length": 25}).json()
        self.assertEqual(data["recordsTotal"], 2)  # owner + insider
        self.assertNotIn("outsider", str(data["data"]))


class RoleTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_create_role_from_grid(self):
        response = self.client.post(reverse("people:role_create"), {"name": "Store keeper", "description": "", "perms": ["outlet.list", "counter.list"]})
        self.assertRedirects(response, reverse("people:roles"))
        with tenant_context(self.tenant):
            self.assertEqual(Role.objects.get(name="Store keeper").permissions, ["counter.list", "outlet.list"])

    def test_unknown_codes_are_rejected(self):
        response = self.client.post(reverse("people:role_create"), {"name": "X", "description": "", "perms": ["platform.everything"]})
        self.assertContains(response, "Select a valid choice")

    def test_editing_keeps_future_codes(self):
        with tenant_context(self.tenant):
            cashier = Role.objects.get(template_key="cashier")
        self.client.post(reverse("people:role_edit", args=[cashier.pk]), {"name": "Cashier", "description": "", "perms": ["outlet.list"]})
        cashier.refresh_from_db()
        self.assertIn("pos.list", cashier.permissions)  # unknown to the grid, so untouched
        self.assertNotIn("pos.create", cashier.permissions)  # on the grid and unticked
        self.assertIn("outlet.list", cashier.permissions)

    def test_duplicate_name_is_a_form_error_per_pharmacy(self):
        response = self.client.post(reverse("people:role_create"), {"name": "Manager", "description": ""})
        self.assertContains(response, "already exists")
        other, other_owner = make_pharmacy("Green Life")
        self.client.force_login(other_owner)
        response = self.client.post(reverse("people:role_create"), {"name": "Pharmacist", "description": ""})
        self.assertRedirects(response, reverse("people:roles"))

    def test_role_in_use_cannot_be_deleted(self):
        user = add_staff(self.tenant, "s@lazz.test", ["outlet.list"])
        role = user.employee.role
        response = self.client.post(reverse("people:role_delete", args=[role.pk]), follow=True)
        self.assertContains(response, "is given to employees")
        user.employee.role = None
        user.employee.save()
        self.client.post(reverse("people:role_delete", args=[role.pk]))
        with tenant_context(self.tenant):
            self.assertFalse(Role.objects.filter(pk=role.pk).exists())

    def test_cannot_touch_another_pharmacys_role(self):
        other, _ = make_pharmacy("Green Life")
        with tenant_context(other):
            foreign = Role.objects.get(template_key="manager")
        self.assertEqual(self.client.get(reverse("people:role_edit", args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("people:role_delete", args=[foreign.pk])).status_code, 404)
        self.assertTrue(Role.all_objects.filter(pk=foreign.pk).exists())
        self.assertEqual(Employee.all_objects.filter(tenant=other).count(), 1)
