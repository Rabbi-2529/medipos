from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

MONEY = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0")}


class CashRegister(TenantOwnedModel):
    """A cashier's till session: opened with a float, closed with a count (Z-report)."""

    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="registers")
    counter = models.ForeignKey("outlets.Counter", on_delete=models.PROTECT, null=True, blank=True, related_name="registers")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_cash = models.DecimalField(_("opening cash"), **MONEY)
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(null=True, blank=True, max_digits=14, decimal_places=2)
    counted_cash = models.DecimalField(_("counted cash"), null=True, blank=True, max_digits=14, decimal_places=2)
    note = models.CharField(_("note"), max_length=255, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [models.UniqueConstraint(fields=["tenant", "outlet", "opened_by"], condition=Q(closed_at__isnull=True),
                                               name="one_open_register_per_user_outlet")]

    def __str__(self):
        return f"{self.outlet} · {self.opened_by} · {self.opened_at:%d %b %H:%M}"

    @property
    def is_open(self):
        return self.closed_at is None


class Sale(TenantOwnedModel):
    class PriceType(models.TextChoices):
        RETAIL = "retail", _("Retail (MRP)")
        WHOLESALE = "wholesale", _("Wholesale")

    number = models.CharField(_("invoice no."), max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="sales")
    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, null=True, blank=True, related_name="sales")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="sales")
    sold_at = models.DateTimeField()
    price_type = models.CharField(max_length=10, choices=PriceType.choices, default=PriceType.RETAIL)
    subtotal = models.DecimalField(**MONEY)          # Σ quantity × unit price
    line_discount = models.DecimalField(**MONEY)     # Σ line discounts
    discount = models.DecimalField(**MONEY)          # cart discount
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(**MONEY)
    rounding = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)             # what the customer must pay
    paid = models.DecimalField(**MONEY)              # paid at the counter
    cash_tendered = models.DecimalField(null=True, blank=True, max_digits=14, decimal_places=2)
    cost_total = models.DecimalField(max_digits=16, decimal_places=4, default=Decimal("0"))
    rx_checked = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-sold_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_sale_number")]
        indexes = [models.Index(fields=["tenant", "outlet", "sold_at"], name="sale_outlet_time")]

    def __str__(self):
        return self.number

    @property
    def due(self):
        return self.total - self.paid

    @property
    def change(self):
        return (self.cash_tendered - self.paid) if self.cash_tendered else Decimal("0")


class SaleLine(TenantOwnedModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    unit_label = models.CharField(max_length=30)          # what the cashier sold by: Piece / Strip / Box
    unit_factor = models.PositiveIntegerField(default=1)  # sale units in that unit
    unit_qty = models.PositiveIntegerField()              # how many of that unit
    quantity = models.PositiveIntegerField()              # sale units out of stock (unit_qty × unit_factor)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)  # per sale unit
    discount = models.DecimalField(**MONEY)
    line_total = models.DecimalField(**MONEY)             # after line discount
    cost_total = models.DecimalField(max_digits=16, decimal_places=4, default=Decimal("0"))
    promotion = models.ForeignKey("sales.Promotion", on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")

    class Meta:
        ordering = ["id"]

    @property
    def net_unit_price(self):
        return self.line_total / self.quantity if self.quantity else Decimal("0")


class Promotion(TenantOwnedModel):
    """An automatic discount the POS puts on matching lines, e.g. "10% off all syrups this month"."""

    class Kind(models.TextChoices):
        PERCENT = "percent", _("% off")
        AMOUNT = "amount", _("Amount off each unit")

    class Scope(models.TextChoices):
        ALL = "all", _("Every medicine")
        CATEGORY = "category", _("One category")
        FORM = "form", _("One dosage form")
        MEDICINES = "medicines", _("Chosen medicines")

    name = models.CharField(_("name"), max_length=100)
    kind = models.CharField(_("discount type"), max_length=10, choices=Kind.choices, default=Kind.PERCENT)
    value = models.DecimalField(_("discount"), max_digits=10, decimal_places=2)
    scope = models.CharField(_("applies to"), max_length=10, choices=Scope.choices, default=Scope.ALL)
    category = models.ForeignKey("items.Category", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("category"))
    dosage_form = models.CharField(_("dosage form"), max_length=20, blank=True)
    medicines = models.ManyToManyField("items.Medicine", blank=True, related_name="+", verbose_name=_("medicines"))
    min_quantity = models.PositiveIntegerField(_("minimum quantity"), default=1,
                                               help_text=_("In sale units (e.g. pieces) on one line. 1 = any quantity."))
    starts_on = models.DateField(_("starts on"), null=True, blank=True)
    ends_on = models.DateField(_("ends on"), null=True, blank=True, help_text=_("Leave empty to run until you switch it off."))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self):
        return self.name

    def runs_on(self, day):
        return self.is_active and (self.starts_on is None or self.starts_on <= day) and (self.ends_on is None or day <= self.ends_on)

    def status(self, day):
        if not self.is_active:
            return "off"
        if self.starts_on and day < self.starts_on:
            return "scheduled"
        if self.ends_on and day > self.ends_on:
            return "ended"
        return "running"

    def discount(self, unit_price, quantity):
        """Discount for `quantity` sale units at `unit_price`; nothing below the minimum quantity."""
        if quantity < self.min_quantity:
            return Decimal("0")
        gross = unit_price * quantity
        off = gross * self.value / 100 if self.kind == self.Kind.PERCENT else self.value * quantity
        return min(off, gross).quantize(Decimal("0.01"))


class Quotation(TenantOwnedModel):
    """A price quote made at the POS. It reserves nothing; selling it later loads it back into the POS."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        SOLD = "sold", _("Sold")

    number = models.CharField(max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="quotations")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="quotations")
    date = models.DateField()
    valid_until = models.DateField()
    price_type = models.CharField(max_length=10, choices=Sale.PriceType.choices, default=Sale.PriceType.RETAIL)
    subtotal = models.DecimalField(**MONEY)
    line_discount = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(**MONEY)
    rounding = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name="quotations")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_quotation_number")]

    def __str__(self):
        return self.number

    @property
    def total_discount(self):
        return self.line_discount + self.discount

    def state(self, day):
        if self.status == self.Status.SOLD:
            return "sold"
        return "expired" if day > self.valid_until else "open"


class QuotationLine(TenantOwnedModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    medicine = models.ForeignKey("items.Medicine", on_delete=models.PROTECT, related_name="+")
    unit_label = models.CharField(max_length=30)
    unit_factor = models.PositiveIntegerField(default=1)
    unit_qty = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    discount = models.DecimalField(**MONEY)
    line_total = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]


class SaleLineBatch(TenantOwnedModel):
    """Which batches a line was taken from (FEFO or chosen), so returns go back to the same batch."""

    line = models.ForeignKey(SaleLine, on_delete=models.CASCADE, related_name="allocations")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)


class SalePayment(TenantOwnedModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)


class HeldCart(TenantOwnedModel):
    """A cart parked at the POS (customer stepped away); recalled later."""

    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.CASCADE, related_name="+")
    held_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    label = models.CharField(max_length=80, blank=True)
    data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]


class SaleReturn(TenantOwnedModel):
    number = models.CharField(max_length=30)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="+")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, null=True, blank=True, related_name="returns")
    date = models.DateField()
    total = models.DecimalField(**MONEY)
    refund_amount = models.DecimalField(**MONEY)
    refund_account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_sale_return_number")]

    def __str__(self):
        return self.number


class SaleReturnLine(TenantOwnedModel):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="lines")
    sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT, related_name="return_lines")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    line_total = models.DecimalField(**MONEY)
