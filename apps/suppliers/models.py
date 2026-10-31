from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Supplier(TenantOwnedModel):
    name = models.CharField(_("name"), max_length=150)
    contact_person = models.CharField(_("contact person"), max_length=100, blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True)
    email = models.EmailField(_("email"), blank=True)
    address = models.TextField(_("address"), blank=True)
    opening_balance = models.DecimalField(
        _("opening balance"), max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text=_("What you owed them when you started using MediPOS. Negative = they owe you."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_supplier_name_per_tenant",
                                               violation_error_message=_("A supplier with this name already exists."))]

    def __str__(self):
        return self.name


class SupplierLedgerEntry(TenantOwnedModel):
    """One line of a supplier's account. credit = we owe more, debit = we owe less. Balance = Σcredit − Σdebit."""

    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening balance")
        PURCHASE = "purchase", _("Purchase")
        PAYMENT = "payment", _("Payment")
        RETURN = "return", _("Purchase return")

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="ledger")
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    ref_type = models.CharField(max_length=30, blank=True)
    ref_id = models.PositiveBigIntegerField(null=True, blank=True)
    ref_label = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["date", "id"]
        verbose_name_plural = "supplier ledger entries"

    def __str__(self):
        return f"{self.supplier} {self.date} {self.get_kind_display()}"


class SupplierPayment(TenantOwnedModel):
    number = models.CharField(max_length=30)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    purchase = models.ForeignKey("purchases.Purchase", on_delete=models.PROTECT, null=True, blank=True, related_name="payments")
    date = models.DateField(_("date"))
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    reference = models.CharField(_("reference"), max_length=100, blank=True, help_text=_("Cheque no., transaction ID…"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_supplier_payment_number")]

    def __str__(self):
        return self.number
