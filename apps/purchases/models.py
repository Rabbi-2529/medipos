from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

MONEY = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0")}


class Purchase(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        RECEIVED = "received", _("Received")

    number = models.CharField(_("number"), max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="purchases", verbose_name=_("outlet"))
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="purchases", verbose_name=_("supplier"))
    supplier_invoice_no = models.CharField(_("supplier invoice no."), max_length=50, blank=True)
    date = models.DateField(_("date"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(_("discount"), **MONEY)
    total = models.DecimalField(**MONEY)
    update_prices = models.BooleanField(_("update medicine prices"), default=True,
                                        help_text=_("Set each medicine's MRP and purchase price from this purchase."))
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_purchase_number")]

    def __str__(self):
        return self.number

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    def paid_amount(self):
        return self.payments.aggregate(t=models.Sum("amount"))["t"] or Decimal("0")

    def returned_amount(self):
        return self.returns.aggregate(t=models.Sum("total"))["t"] or Decimal("0")

    def due_amount(self):
        return self.total - self.returned_amount() - self.paid_amount()


class PurchaseLine(TenantOwnedModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    batch_no = models.CharField(max_length=40, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    conversion = models.PositiveIntegerField(default=1, help_text="Sale units per purchase unit when bought.")
    packs = models.PositiveIntegerField(default=0, help_text="Purchase units (e.g. boxes).")
    units = models.PositiveIntegerField(default=0, help_text="Extra loose sale units.")
    free_units = models.PositiveIntegerField(default=0, help_text="Bonus sale units received free.")
    pack_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(**MONEY)
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["id"]

    @property
    def paid_units(self):
        return self.packs * self.conversion + self.units

    @property
    def total_units(self):
        return self.paid_units + self.free_units

    @property
    def unit_cost(self):
        """Cost per sale unit after free goods spread the price."""
        return (self.line_total / self.total_units) if self.total_units else Decimal("0")


class PurchaseReturn(TenantOwnedModel):
    number = models.CharField(max_length=30)
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="returns")
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="returns")
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    total = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_purchase_return_number")]

    def __str__(self):
        return self.number


class PurchaseReturnLine(TenantOwnedModel):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="lines")
    purchase_line = models.ForeignKey(PurchaseLine, on_delete=models.PROTECT, related_name="return_lines")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)
    line_total = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]
