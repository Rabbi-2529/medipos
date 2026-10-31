from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

RESOURCES = ("outlets", "users", "medicines")


class Plan(TimeStampedModel):
    """A subscription package sold by the platform. An empty limit means unlimited."""

    name = models.CharField(max_length=60)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=255, blank=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    price_yearly = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text=_("Leave empty to charge 12 × the monthly price."),
    )
    max_outlets = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    max_users = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    max_medicines = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    is_public = models.BooleanField(default=True, help_text=_("Offered on the sign-up page."))
    is_active = models.BooleanField(default=True, help_text=_("Inactive plans can't be chosen or sold."))
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_monthly"]

    def __str__(self):
        return self.name

    def limit(self, resource):
        return getattr(self, f"max_{resource}")

    def price_for(self, months):
        if months == 12 and self.price_yearly is not None:
            return self.price_yearly
        return self.price_monthly * months


class SubscriptionPayment(TimeStampedModel):
    """A payment the platform received from a pharmacy. Each one extends paid_until."""

    class Method(models.TextChoices):
        CASH = "cash", _("Cash")
        BANK = "bank", _("Bank transfer")
        BKASH = "bkash", _("bKash")
        NAGAD = "nagad", _("Nagad")
        OTHER = "other", _("Other")

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")
    months = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(36)])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=100, blank=True, help_text=_("Transaction ID, cheque no., etc."))
    note = models.CharField(max_length=255, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-paid_at", "-id"]

    def __str__(self):
        return f"{self.tenant} · {self.amount} ({self.period_start} – {self.period_end})"
