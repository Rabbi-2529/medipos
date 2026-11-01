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


@override_settings(MEDIA_ROOT=MEDIA)
class InvoiceSettingsTests(TestCase):
    """Invoice design, the language bills print in, and the POS printing settings."""

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.bkash = PaymentAccount.objects.get(name="bKash")

    def save(self, **overrides):
        data = {"receipt_language": "auto", "receipt_size": "80", "receipt_font": "normal", "qr_code": "none", "show_logo": "on",
                "show_address": "on", "show_license": "on", "show_cashier": "on", "show_customer_phone": "on"}
        data.update(overrides)
        return self.client.post(reverse("setup:invoice"), data)

    def preview(self, size):
        return self.client.get(reverse("setup:invoice_preview"), {"size": size})

    def test_design_language_qr_and_letterhead(self):
        self.assertContains(self.client.get(reverse("setup:invoice")), "Preview")
        sample = self.preview("80")
        self.assertEqual(sample["X-Frame-Options"], "SAMEORIGIN")  # the settings page shows it in a frame
        self.assertContains(sample, "Rahim Uddin")
        self.assertContains(sample, "Cashier")
        self.assertNotContains(sample, "NP2041")  # batches are off by default

        self.assertContains(self.save(qr_code="text"), "Write what the QR code should hold.")
        response = self.save(invoice_title="Cash memo", receipt_size="a4", receipt_font="large", show_cashier="", show_batch="on",
                             qr_code="text", qr_text="https://lazz.example", thank_you="Come again", invoice_terms="No returns after 7 days.",
                             auto_print="on", pos_default_account=self.bkash.pk, letterhead=tiny_png(), preview_size="a4")
        self.assertRedirects(response, f"{reverse('setup:invoice')}?size=a4")
        settings = get_business_settings(self.tenant)
        self.assertEqual((settings.invoice_title, settings.receipt_size, settings.auto_print, settings.pos_default_account),
                         ("Cash memo", "a4", True, self.bkash))
        a4 = self.preview("a4")
        for text in ("Cash memo", "Come again", "No returns after 7 days.", "Batch: NP2041", "<svg", "letterhead"):
            self.assertContains(a4, text)
        self.assertNotContains(a4, "Cashier:")
        self.assertNotContains(self.preview("80"), "No returns after 7 days.")  # terms are for A4

        # Bangla bills whatever the cashier's own language.
        self.save(receipt_language="bn", receipt_bangla_digits="on")
        bangla = self.preview("80")
        self.assertContains(bangla, 'lang="bn"')
        self.assertContains(bangla, "০১৭১১২২৩৩৪৪")  # the customer's phone in Bangla digits

        self.client.post(reverse("setup:invoice"), {"action": "remove_letterhead"})
        self.assertFalse(get_business_settings(self.tenant).letterhead)

    def test_pos_gets_printing_settings(self):
        self.save(auto_print="on", pos_default_account=self.bkash.pk)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, '"autoPrint": true')
        self.assertContains(page, f'"defaultAccount": {self.bkash.pk}')

    def test_view_only_staff(self):
        viewer = add_staff(self.tenant, "v@x.test", ["settings.show"])
        self.client.force_login(viewer)
        self.assertTrue(self.client.get(reverse("setup:invoice")).context["form"].fields["invoice_title"].disabled)
        self.assertEqual(self.preview("58").status_code, 200)
        self.assertEqual(self.save().status_code, 403)

    def test_real_receipt_follows_the_settings(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.core.tests.factories import make_medicine
        from apps.customers.models import Customer
        from apps.outlets.models import Outlet
        from apps.sales.services import complete_sale, start_register
        from apps.stock.models import StockMovement
        from apps.stock.services import get_or_create_batch, post_movement

        with tenant_context(self.tenant):
            outlet = Outlet.objects.get()
            napa = make_medicine(self.tenant, "Napa", sale_price="10.00")
            batch = get_or_create_batch(napa, "NB77", timezone.localdate() + timedelta(days=300), Decimal("5"))
            post_movement(outlet=outlet, batch=batch, quantity=50, kind=StockMovement.Kind.OPENING)
            register = start_register(self.owner, outlet, "0")
            rahim = Customer.objects.create(name="Rahim", phone="01712345678", opening_balance=Decimal("100"))
            sale = complete_sale(tenant=self.tenant, outlet=outlet, user=self.owner, register=register, customer=rahim,
                                 lines=[{"medicine": napa.pk, "qty": 3}], payments=[])  # 30 more on due
        self.save(receipt_language="en", show_batch="on", show_customer_due="on")
        receipt = self.client.get(reverse("sales:receipt", args=[sale.pk]))
        self.assertEqual(receipt["X-Frame-Options"], "SAMEORIGIN")  # the POS prints it from a hidden frame
        self.assertContains(receipt, "Total due (all bills)")
        self.assertContains(receipt, "Batch: NB77")
        self.save(receipt_language="bn")
        self.assertContains(self.client.get(reverse("sales:receipt", args=[sale.pk])), 'lang="bn"')
