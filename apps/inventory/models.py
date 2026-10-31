from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

MONEY = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0")}
COST = {"max_digits": 14, "decimal_places": 4, "default": Decimal("0")}


class Transfer(TenantOwnedModel):
    """Stock sent from one outlet to another. It leaves on dispatch and arrives when the other side receives it."""

    class Status(models.TextChoices):
        IN_TRANSIT = "in_transit", _("In transit")
        RECEIVED = "received", _("Received")
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(_("number"), max_length=30)
    from_outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="transfers_out", verbose_name=_("from"))
    to_outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="transfers_in", verbose_name=_("to"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_TRANSIT)
    total_cost = models.DecimalField(**MONEY)
    note = models.CharField(_("note"), max_length=255, blank=True)
    dispatched_at = models.DateTimeField(auto_now_add=True)
    dispatched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)  # received or cancelled
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-dispatched_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_transfer_number")]

    def __str__(self):
        return self.number

    @property
    def in_transit(self):
        return self.status == self.Status.IN_TRANSIT


class TransferLine(TenantOwnedModel):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()  # sale units
    unit_cost = models.DecimalField(**COST)

    class Meta:
        ordering = ["id"]


class WriteOff(TenantOwnedModel):
    """Stock thrown away: damaged, expired, lost. Its cost is a loss."""

    class Reason(models.TextChoices):
        EXPIRED = "expired", _("Expired")
        DAMAGED = "damaged", _("Damaged")
        LOST = "lost", _("Lost / stolen")
        OTHER = "other", _("Other")

    number = models.CharField(_("number"), max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="write_offs", verbose_name=_("outlet"))
    date = models.DateField(_("date"))
    reason = models.CharField(_("reason"), max_length=10, choices=Reason.choices, default=Reason.DAMAGED)
    note = models.CharField(_("note"), max_length=255, blank=True)
    total_cost = models.DecimalField(**MONEY)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_write_off_number")]

    def __str__(self):
        return self.number


class WriteOffLine(TenantOwnedModel):
    write_off = models.ForeignKey(WriteOff, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(**COST)
    line_total = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]


class StockCount(TenantOwnedModel):
    """A shelf count: what the system said, what was counted, and the adjustment that made them match."""

    number = models.CharField(_("number"), max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="stock_counts", verbose_name=_("outlet"))
    date = models.DateField(_("date"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    gain_cost = models.DecimalField(**MONEY)  # value of units found
    loss_cost = models.DecimalField(**MONEY)  # value of units missing
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_stock_count_number")]

    def __str__(self):
        return self.number

    @property
    def net_cost(self):
        return self.gain_cost - self.loss_cost


class StockCountLine(TenantOwnedModel):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    system_quantity = models.IntegerField()
    counted_quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(**COST)

    class Meta:
        ordering = ["id"]

    @property
    def difference(self):
        return self.counted_quantity - self.system_quantity
