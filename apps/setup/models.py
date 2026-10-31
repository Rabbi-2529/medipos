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

DATE_FORMATS = [
    ("d M Y", "31 Oct 2026"),
    ("d/m/Y", "31/10/2026"),
    ("d-m-Y", "31-10-2026"),
    ("Y-m-d", "2026-10-31"),
    ("m/d/Y", "10/31/2026"),
]


def logo_path(instance, filename):
    return f"tenants/{instance.tenant_id}/logo/{filename}"


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
