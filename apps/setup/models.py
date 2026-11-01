from decimal import Decimal

from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

LANGUAGE_CHOICES = [("en", "English"), ("bn", "বাংলা")]


class ReceiptSize(models.TextChoices):
    MM58 = "58", _("58 mm thermal")
    MM80 = "80", _("80 mm thermal")
    A4 = "a4", _("A4 invoice")

class ReceiptLanguage(models.TextChoices):
    AUTO = "auto", _("The language of whoever prints it")
    EN = "en", "English"
    BN = "bn", "বাংলা"


class QrCode(models.TextChoices):
    NONE = "none", _("No QR code")
    BILL = "bill", _("Link to the customer's copy of the bill")
    TEXT = "text", _("My own text or link")


class FontSize(models.TextChoices):
    SMALL = "small", _("Small")
    NORMAL = "normal", _("Normal")
    LARGE = "large", _("Large")


DATE_FORMATS = [
    ("d M Y", "31 Oct 2026"),
    ("d/m/Y", "31/10/2026"),
    ("d-m-Y", "31-10-2026"),
    ("Y-m-d", "2026-10-31"),
    ("m/d/Y", "10/31/2026"),
]


def logo_path(instance, filename):
    return f"tenants/{instance.tenant_id}/logo/{filename}"


def letterhead_path(instance, filename):
    return f"tenants/{instance.tenant_id}/letterhead/{filename}"


class BusinessSettings(TenantOwnedModel):
    """One row per pharmacy. Business name/phone/address live on the Tenant itself."""

    class CurrencyPosition(models.TextChoices):
        BEFORE = "before", _("Before amount (৳100)")
        AFTER = "after", _("After amount (100৳)")

    logo = models.ImageField(_("logo"), upload_to=logo_path, blank=True)
    drug_license_no = models.CharField(_("drug license no."), max_length=50, blank=True)
    vat_number = models.CharField(_("BIN / VAT reg. no."), max_length=50, blank=True)
    currency_symbol = models.CharField(_("currency symbol"), max_length=8, default="৳")
    currency_code = models.CharField(_("currency code"), max_length=3, default="BDT")
    currency_position = models.CharField(
        _("symbol position"), max_length=6, choices=CurrencyPosition.choices, default=CurrencyPosition.BEFORE
    )
    decimal_places = models.PositiveSmallIntegerField(_("decimal places"), default=2, validators=[MaxValueValidator(3)])
    date_format = models.CharField(_("date format"), max_length=10, choices=DATE_FORMATS, default="d M Y")
    invoice_footer = models.TextField(_("invoice footer"), blank=True, help_text=_("Printed at the bottom of every invoice."))

    # Selling
    vat_percent = models.DecimalField(_("VAT %"), max_digits=5, decimal_places=2, default=Decimal("0"),
                                      validators=[MaxValueValidator(100)], help_text=_("Added on top of the sale. 0 = no VAT."))
    round_total = models.BooleanField(_("round sale totals to whole taka"), default=True)
    receipt_size = models.CharField(_("receipt paper"), max_length=4, choices=ReceiptSize.choices, default=ReceiptSize.MM80)

    # Language
    default_language = models.CharField(_("default language"), max_length=5, choices=LANGUAGE_CHOICES, default="en",
                                        help_text=_("Staff who haven't picked a language see this one."))
    receipt_bangla_digits = models.BooleanField(_("Bangla digits on receipts"), default=False,
                                                help_text=_("Prints ১২৩ instead of 123 when the receipt is in Bangla."))

    # Invoice design (receipts, A4 invoices, quotations and the customer's bill link)
    invoice_title = models.CharField(_("invoice title"), max_length=40, blank=True, help_text=_("Empty = “Invoice”. e.g. Cash memo, Bill."))
    receipt_language = models.CharField(_("print invoices in"), max_length=5, choices=ReceiptLanguage.choices, default=ReceiptLanguage.AUTO)
    show_logo = models.BooleanField(_("Logo"), default=True)
    letterhead = models.ImageField(_("A4 letterhead"), upload_to=letterhead_path, blank=True,
                                   help_text=_("A wide image across the top of A4 invoices, in place of the name and address."))
    show_address = models.BooleanField(_("Outlet address and phone"), default=True)
    show_license = models.BooleanField(_("Drug license and BIN"), default=True)
    show_cashier = models.BooleanField(_("Cashier"), default=True)
    show_customer_phone = models.BooleanField(_("Customer's phone"), default=True)
    show_generic = models.BooleanField(_("Generic name under each medicine"), default=False)
    show_batch = models.BooleanField(_("Batch and expiry under each medicine"), default=False)
    show_customer_due = models.BooleanField(_("Customer's total due"), default=True)
    show_points = models.BooleanField(_("Loyalty points"), default=True)
    thank_you = models.CharField(_("closing line"), max_length=120, blank=True, help_text=_("Empty = “Thank you. Get well soon!”"))
    invoice_terms = models.TextField(_("terms and conditions"), blank=True,
                                     help_text=_("Printed small at the bottom of A4 invoices and quotations, e.g. your returns policy."))
    qr_code = models.CharField(_("QR code"), max_length=5, choices=QrCode.choices, default=QrCode.NONE)
    qr_text = models.CharField(_("QR code text"), max_length=300, blank=True,
                               help_text=_("e.g. your website, Facebook page or bKash merchant number."))
    receipt_font = models.CharField(_("text size on thermal receipts"), max_length=6, choices=FontSize.choices, default=FontSize.NORMAL)

    # POS and printing
    auto_print = models.BooleanField(_("Print the receipt after each sale"), default=False,
                                     help_text=_("The print dialog opens by itself after “Complete sale”."))
    pos_default_account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name="+", verbose_name=_("payment method the POS starts with"),
                                            help_text=_("Empty = cash."))

    class Meta:
        verbose_name_plural = "business settings"
        constraints = [models.UniqueConstraint(fields=["tenant"], name="one_settings_per_tenant")]

    def __str__(self):
        return f"Settings for tenant {self.tenant_id}"


class Sequence(TenantOwnedModel):
    """Running document numbers per pharmacy, e.g. invoice INV00001."""

    key = models.CharField(max_length=30)
    prefix = models.CharField(_("prefix"), max_length=10, blank=True)
    next_value = models.PositiveIntegerField(_("next number"), default=1)
    padding = models.PositiveSmallIntegerField(_("digits"), default=5, validators=[MaxValueValidator(10)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="uniq_sequence_key_per_tenant")]

    def __str__(self):
        return f"{self.key}: {self.format(self.next_value)}"

    def format(self, value):
        return f"{self.prefix}{value:0{self.padding}d}"
