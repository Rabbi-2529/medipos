from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.core.tenancy import TenantMissingError, bypass_tenant, get_current_tenant, tenant_context
from apps.core.tests.testapp.models import Widget
from apps.tenants.models import Tenant


class TenantScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Tenant.objects.create(name="Pharmacy A", slug="a")
        cls.b = Tenant.objects.create(name="Pharmacy B", slug="b")
        with tenant_context(cls.a):
            cls.widget_a = Widget.objects.create(name="a1")
        with tenant_context(cls.b):
            cls.widget_b = Widget.objects.create(name="b1")

    def test_no_active_tenant_sees_nothing(self):
        self.assertEqual(Widget.objects.count(), 0)

    def test_tenant_sees_only_its_own_rows(self):
        with tenant_context(self.a):
            self.assertEqual(list(Widget.objects.values_list("name", flat=True)), ["a1"])

    def test_other_tenants_row_cannot_be_fetched_by_id(self):
        with tenant_context(self.a), self.assertRaises(Widget.DoesNotExist):
            Widget.objects.get(pk=self.widget_b.pk)

    def test_save_stamps_the_active_tenant(self):
        with tenant_context(self.b):
            widget = Widget.objects.create(name="b2")
        self.assertEqual(widget.tenant, self.b)

    def test_save_without_active_tenant_is_refused(self):
        with self.assertRaises(TenantMissingError):
            Widget.objects.create(name="orphan")

    def test_explicit_tenant_is_kept_on_save(self):
        with tenant_context(self.a):
            widget = Widget(name="b3", tenant=self.b)
            widget.save()
        self.assertEqual(widget.tenant, self.b)

    def test_bypass_and_all_objects_see_every_tenant(self):
        with bypass_tenant():
            self.assertEqual(Widget.objects.count(), 2)
        self.assertEqual(Widget.all_objects.count(), 2)

    def test_context_is_restored_after_block(self):
        with tenant_context(self.a):
            with tenant_context(self.b):
                self.assertEqual(get_current_tenant(), self.b)
            self.assertEqual(get_current_tenant(), self.a)
        self.assertIsNone(get_current_tenant())


@override_settings(ROOT_URLCONF="apps.core.tests.testapp.urls")
class TenantMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Tenant.objects.create(name="Pharmacy A", slug="a")
        cls.b = Tenant.objects.create(name="Pharmacy B", slug="b")
        with tenant_context(cls.a):
            Widget.objects.create(name="a1")
        with tenant_context(cls.b):
            Widget.objects.create(name="b1")
        cls.user_a = User.objects.create_user("a@x.test", "pw", tenant=cls.a)
        cls.admin = User.objects.create_superuser("admin@x.test", "pw")

    def test_request_is_scoped_to_users_tenant(self):
        self.client.force_login(self.user_a)
        self.assertEqual(self.client.get("/widgets/").content, b"a1")

    def test_platform_admin_request_sees_no_tenant_rows(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/widgets/").content, b"")

    def test_anonymous_request_sees_no_tenant_rows(self):
        self.assertEqual(self.client.get("/widgets/").content, b"")

    def test_tenant_is_cleared_after_request(self):
        self.client.force_login(self.user_a)
        self.client.get("/widgets/")
        self.assertIsNone(get_current_tenant())
