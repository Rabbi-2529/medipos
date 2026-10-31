from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class AccessState(models.TextChoices):
    """What a pharmacy can do right now, derived from its status and dates."""

    TRIAL = "trial", _("Trial")
    ACTIVE = "active", _("Active")
    GRACE = "grace", _("Payment due")
    EXPIRED = "expired", _("Expired")
    SUSPENDED = "suspended", _("Suspended")


OPEN_STATES = frozenset({AccessState.TRIAL, AccessState.ACTIVE, AccessState.GRACE})


def _today_and_grace_start(now):
    today = timezone.localdate(now)
    return today, today - timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)


class TenantQuerySet(models.QuerySet):
    def in_state(self, state, now=None):
        """Database version of Tenant.access_state(); the two must agree."""
        now = now or timezone.now()
        today, grace_start = _today_and_grace_start(now)
        trial, active, suspended = Tenant.Status.TRIAL, Tenant.Status.ACTIVE, Tenant.Status.SUSPENDED
        conditions = {
            AccessState.TRIAL: Q(status=trial) & (Q(trial_ends_at__isnull=True) | Q(trial_ends_at__gt=now)),
            AccessState.ACTIVE: Q(status=active) & (Q(paid_until__isnull=True) | Q(paid_until__gte=today)),
            AccessState.GRACE: Q(status=active, paid_until__lt=today, paid_until__gte=grace_start),
            AccessState.EXPIRED: Q(status=trial, trial_ends_at__lte=now) | Q(status=active, paid_until__lt=grace_start),
            AccessState.SUSPENDED: Q(status=suspended),
        }
        return self.filter(conditions[AccessState(state)])

    def expiring_within(self, days, now=None):
        """Open trials or paid periods that end in the next `days` days."""
        now = now or timezone.now()
        today = timezone.localdate(now)
        return self.filter(
            Q(status=Tenant.Status.TRIAL, trial_ends_at__gt=now, trial_ends_at__lte=now + timedelta(days=days))
            | Q(status=Tenant.Status.ACTIVE, paid_until__gte=today, paid_until__lte=today + timedelta(days=days))
        )


class Tenant(TimeStampedModel):
    """One subscribing pharmacy business. Its outlets, stock and sales all hang off it."""

    class Status(models.TextChoices):
        TRIAL = "trial", _("Trial")
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")

    name = models.CharField(_("business name"), max_length=150)
    slug = models.SlugField(unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    plan = models.ForeignKey(
        "billing.Plan", on_delete=models.PROTECT, null=True, blank=True, related_name="tenants"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.TRIAL)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    paid_until = models.DateField(
        null=True, blank=True, help_text=_("Last day covered by payments. Empty = no end (complimentary).")
    )
    suspended_reason = models.CharField(max_length=255, blank=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def access_state(self, now=None):
        now = now or timezone.now()
        if self.status == self.Status.SUSPENDED:
            return AccessState.SUSPENDED
        if self.status == self.Status.TRIAL:
            if self.trial_ends_at is None or now < self.trial_ends_at:
                return AccessState.TRIAL
            return AccessState.EXPIRED
        if self.paid_until is None:
            return AccessState.ACTIVE
        today, grace_start = _today_and_grace_start(now)
        if self.paid_until >= today:
            return AccessState.ACTIVE
        if self.paid_until >= grace_start:
            return AccessState.GRACE
        return AccessState.EXPIRED

    @property
    def has_access(self):
        return self.access_state() in OPEN_STATES

    def ends_on(self):
        """Date the current trial or paid period ends (None if open-ended)."""
        if self.status == self.Status.TRIAL and self.trial_ends_at:
            return timezone.localdate(self.trial_ends_at)
        if self.status == self.Status.ACTIVE:
            return self.paid_until
        return None

    def days_left(self, now=None):
        end = self.ends_on()
        if end is None:
            return None
        return (end - timezone.localdate(now or timezone.now())).days

    @property
    def owner(self):
        return self.users.filter(is_owner=True).order_by("date_joined").first()


class TenantEvent(models.Model):
    """Audit trail of subscription changes, shown on the platform console."""

    class Kind(models.TextChoices):
        SIGNUP = "signup", _("Signed up")
        PAYMENT = "payment", _("Payment recorded")
        PLAN_CHANGED = "plan_changed", _("Plan changed")
        TRIAL_EXTENDED = "trial_extended", _("Trial extended")
        SUSPENDED = "suspended", _("Suspended")
        REACTIVATED = "reactivated", _("Reactivated")

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=255)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tenant}: {self.message}"
