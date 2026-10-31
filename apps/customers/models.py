from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Customer(TenantOwnedModel):
    name = models.CharField(_("name"), max_length=150)
    phone = models.CharField(_("phone"), max_length=30, blank=True)
    email = models.EmailField(_("email"), blank=True)
    address = models.TextField(_("address"), blank=True)
    opening_balance = models.DecimalField(
        _("opening due"), max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text=_("What they owed you when you started using MediPOS."),
    )
    credit_limit = models.DecimalField(
        _("credit limit"), max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
        help_text=_("Most they may owe. Empty = no limit, 0 = no credit."),
    )
    default_discount = models.DecimalField(
        _("default discount %"), max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    notes = models.CharField(_("notes"), max_length=255, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "phone"], condition=~Q(phone=""), name="uniq_customer_phone_per_tenant",
                                    violation_error_message=_("A customer with this phone number already exists.")),
        ]
        indexes = [models.Index(fields=["tenant", "phone"], name="customer_tenant_phone")]

    def __str__(self):
        return f"{self.name} · {self.phone}" if self.phone else self.name


class CustomerLedgerEntry(TenantOwnedModel):
    """debit = customer owes more (sale), credit = owes less (payment, return). Balance = Σdebit − Σcredit."""

    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening due")
        SALE = "sale", _("Sale")
        PAYMENT = "payment", _("Payment")
        RETURN = "return", _("Sale return")
        REFUND = "refund", _("Refund")

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="ledger")
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
        verbose_name_plural = "customer ledger entries"


class CustomerReceipt(TenantOwnedModel):
    """Money collected against a customer's due."""

    number = models.CharField(max_length=30)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="receipts")
    sale = models.ForeignKey("sales.Sale", on_delete=models.PROTECT, null=True, blank=True, related_name="receipts")
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    register = models.ForeignKey("sales.CashRegister", on_delete=models.PROTECT, null=True, blank=True, related_name="receipts")
    date = models.DateField(_("date"))
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, related_name="+", verbose_name=_("received in"))
    reference = models.CharField(_("reference"), max_length=100, blank=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_customer_receipt_number")]

    def __str__(self):
        return self.number
