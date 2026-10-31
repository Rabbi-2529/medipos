import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_pharmacy
from apps.people.models import Employee, Role

from .models import BusinessSettings, Sequence
from .services import format_money, get_business_settings, next_code, provision_tenant

MEDIA = tempfile.mkdtemp()


def tiny_png():
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "purple").save(buffer, "PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")


class ProvisioningTests(TestCase):
    def test_new_pharmacy_gets_starter_data_once(self):
        tenant, owner = make_pharmacy()
        provision_tenant(tenant)  # re-running adds nothing
        with tenant_context(tenant):
            self.assertEqual(BusinessSettings.objects.count(), 1)
            self.assertEqual(Sequence.objects.get(key="invoice").format(1), "INV00001")
            self.assertEqual(list(PaymentAccount.objects.values_list("name", flat=True)), ["Cash", "bKash", "Nagad"])
            self.assertEqual(sorted(Role.objects.values_list("template_key", flat=True)), ["cashier", "manager"])
            self.assertTrue(Employee.objects.get(user=owner).all_outlets)


class NumberingAndFormattingTests(TestCase):
    def test_next_code(self):
        tenant, _ = make_pharmacy()
        self.assertEqual([next_code(tenant, "invoice") for _ in range(2)], ["INV00001", "INV00002"])
        self.assertEqual(next_code(tenant, "purchase", prefix="PUR-", padding=3), "PUR-001")

    def test_format_money(self):
        settings = BusinessSettings(currency_symbol="৳", decimal_places=2)
        self.assertEqual(format_money("1234.5", settings), "৳1,234.50")
        settings.currency_position, settings.decimal_places = "after", 0
        self.assertEqual(format_money(1234.5, settings), "1,234৳")


@override_settings(MEDIA_ROOT=MEDIA)
class SettingsPageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()

    def _post(self, **overrides):
        data = {
            "profile-name": "Lazz Pharma Ltd", "profile-phone": "01700000000", "profile-email": "", "profile-address": "",
            "shop-drug_license_no": "DL-123", "shop-vat_number": "", "shop-currency_symbol": "Tk", "shop-currency_code": "BDT",
            "shop-currency_position": "before", "shop-decimal_places": "0", "shop-date_format": "d/m/Y", "shop-invoice_footer": "",
            "invoice-prefix": "LZ-", "invoice-next_value": "100", "invoice-padding": "4",
            "shop-vat_percent": "0", "shop-round_total": "on", "shop-receipt_size": "80", "shop-default_language": "en",
        }
        data.update(overrides)
        return self.client.post(reverse("setup:settings"), data)

    def test_owner_updates_settings_and_logo(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self._post(**{"shop-logo": tiny_png()}), reverse("setup:settings"))
        self.tenant.refresh_from_db()
        settings = get_business_settings(self.tenant)
        self.assertEqual((self.tenant.name, settings.drug_license_no, settings.currency_symbol), ("Lazz Pharma Ltd", "DL-123", "Tk"))
        self.assertTrue(settings.logo.name.startswith(f"tenants/{self.tenant.pk}/logo/"))
        self.assertEqual(next_code(self.tenant, "invoice"), "LZ-0100")

    def test_view_only_staff(self):
        viewer = add_staff(self.tenant, "v@x.test", ["settings.show"])
        self.client.force_login(viewer)
        response = self.client.get(reverse("setup:settings"))
        self.assertTrue(response.context["profile"].fields["name"].disabled)
        self.assertNotContains(response, "Save settings")
        self.assertEqual(self._post().status_code, 403)
