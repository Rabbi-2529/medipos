from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Batch(TenantOwnedModel):
    """One manufacturing batch of a medicine. Shared by all outlets; quantities live in BatchStock."""

    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="batches")
    batch_no = models.CharField(_("batch no."), max_length=40, blank=True)
    expiry_date = models.DateField(_("expiry"), null=True, blank=True)
    unit_cost = models.DecimalField(_("cost per unit"), max_digits=14, decimal_places=4, default=Decimal("0"),
                                    help_text=_("From the latest purchase of this batch."))

    class Meta:
        ordering = ["expiry_date", "batch_no"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "medicine", "batch_no", "expiry_date"],
                                    condition=Q(expiry_date__isnull=False), name="uniq_batch_with_expiry"),
            models.UniqueConstraint(fields=["tenant", "medicine", "batch_no"],
                                    condition=Q(expiry_date__isnull=True), name="uniq_batch_without_expiry"),
        ]

    def __str__(self):
        label = self.batch_no or str(_("No batch no."))
        return f"{label} · {self.expiry_date:%m/%Y}" if self.expiry_date else label


class BatchStock(TenantOwnedModel):
    """How many sale units of a batch an outlet has right now. Only StockService changes it."""

    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="batch_stocks")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="stocks")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="batch_stocks")
    quantity = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["outlet", "batch"], name="uniq_stock_per_outlet_batch"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="stock_never_negative"),
        ]
        indexes = [models.Index(fields=["tenant", "outlet", "medicine"], name="stock_outlet_medicine")]

    def __str__(self):
        return f"{self.batch} @ {self.outlet}: {self.quantity}"


class StockMovement(TenantOwnedModel):
    """Append-only history of every stock change. Never edited or deleted."""

    class Kind(models.TextChoices):
        OPENING = "opening", _("Opening stock")
        PURCHASE = "purchase", _("Purchase")
        PURCHASE_RETURN = "purchase_return", _("Purchase return")
        SALE = "sale", _("Sale")
        SALE_RETURN = "sale_return", _("Sale return")
        ADJUSTMENT = "adjustment", _("Adjustment")
        TRANSFER_IN = "transfer_in", _("Transfer in")
        TRANSFER_OUT = "transfer_out", _("Transfer out")
        DAMAGE = "damage", _("Damage / expired")

    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="movements")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.IntegerField(help_text="Signed: + in, - out (sale units).")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    balance_after = models.IntegerField(help_text="This batch's stock at this outlet after the movement.")
    ref_type = models.CharField(max_length=30, blank=True)
    ref_id = models.PositiveBigIntegerField(null=True, blank=True)
    ref_label = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "medicine", "created_at"], name="movement_medicine_time"),
            models.Index(fields=["tenant", "ref_type", "ref_id"], name="movement_reference"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity:+d} ({self.batch})"
