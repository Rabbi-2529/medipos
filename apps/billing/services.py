"""Subscription lifecycle. Every change is logged as a TenantEvent."""
import calendar
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.money import format_taka
from apps.tenants.models import AccessState, Tenant, TenantEvent

from .limits import RESOURCE_LABELS, over_limits
from .models import SubscriptionPayment


class SubscriptionError(Exception):
    """A user-facing reason why a subscription change was refused."""


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _log(tenant, kind, message, actor):
    TenantEvent.objects.create(tenant=tenant, kind=kind, message=message, actor=actor)


def _locked(tenant):
    return Tenant.objects.select_for_update().get(pk=tenant.pk)


@transaction.atomic
def record_payment(tenant, *, plan, months, method, amount=None, reference="", note="", actor=None, now=None):
    """Record a payment and extend the paid period.

    The new period starts the day after the current paid period, or after an
    unfinished trial, or today — whichever is latest. Payment always activates.
    """
    now = now or timezone.now()
    tenant = _locked(tenant)
    today = timezone.localdate(now)
    start = today
    if tenant.paid_until and tenant.paid_until >= today:
        start = tenant.paid_until + timedelta(days=1)
    elif tenant.status == Tenant.Status.TRIAL and tenant.trial_ends_at and tenant.trial_ends_at > now:
        start = timezone.localdate(tenant.trial_ends_at)
    end = add_months(start, months) - timedelta(days=1)
    if amount is None:
        amount = plan.price_for(months)

    payment = SubscriptionPayment.objects.create(
        tenant=tenant, plan=plan, months=months, amount=amount, method=method,
        reference=reference, note=note, period_start=start, period_end=end,
        paid_at=now, recorded_by=actor,
    )
    tenant.plan = plan
    tenant.paid_until = end
    tenant.status = Tenant.Status.ACTIVE
    tenant.suspended_reason = ""
    tenant.save(update_fields=["plan", "paid_until", "status", "suspended_reason", "updated_at"])
    _log(tenant, TenantEvent.Kind.PAYMENT,
         _("%(amount)s for %(months)s month(s) of %(plan)s, paid until %(end)s")
         % {"amount": format_taka(amount), "months": months, "plan": plan.name, "end": end.strftime("%d %b %Y")},
         actor)
    return payment


@transaction.atomic
def change_plan(tenant, plan, *, actor=None):
    if not plan.is_active:
        raise SubscriptionError(_("%(plan)s is not an active plan.") % {"plan": plan.name})
    tenant = _locked(tenant)
    if tenant.plan_id == plan.pk:
        return tenant
    too_big = over_limits(tenant, plan)
    if too_big:
        parts = ", ".join(f"{row['used']} {RESOURCE_LABELS[row['key']].lower()} (limit {row['limit']})" for row in too_big)
        raise SubscriptionError(_("This pharmacy uses more than %(plan)s allows: %(parts)s.") % {"plan": plan.name, "parts": parts})
    old = tenant.plan.name if tenant.plan else _("no plan")
    tenant.plan = plan
    tenant.save(update_fields=["plan", "updated_at"])
    _log(tenant, TenantEvent.Kind.PLAN_CHANGED, _("Plan changed from %(old)s to %(new)s") % {"old": old, "new": plan.name}, actor)
    return tenant


@transaction.atomic
def extend_trial(tenant, days, *, actor=None, now=None):
    now = now or timezone.now()
    tenant = _locked(tenant)
    if tenant.status != Tenant.Status.TRIAL:
        raise SubscriptionError(_("Only pharmacies on trial can have their trial extended."))
    base = max(now, tenant.trial_ends_at) if tenant.trial_ends_at else now
    tenant.trial_ends_at = base + timedelta(days=days)
    tenant.save(update_fields=["trial_ends_at", "updated_at"])
    _log(tenant, TenantEvent.Kind.TRIAL_EXTENDED,
         _("Trial extended by %(days)s days, now ends %(end)s")
         % {"days": days, "end": timezone.localdate(tenant.trial_ends_at).strftime("%d %b %Y")},
         actor)
    return tenant


@transaction.atomic
def suspend(tenant, reason, *, actor=None):
    tenant = _locked(tenant)
    if tenant.status == Tenant.Status.SUSPENDED:
        raise SubscriptionError(_("This pharmacy is already suspended."))
    tenant.status = Tenant.Status.SUSPENDED
    tenant.suspended_reason = reason
    tenant.save(update_fields=["status", "suspended_reason", "updated_at"])
    _log(tenant, TenantEvent.Kind.SUSPENDED, _("Suspended: %(reason)s") % {"reason": reason}, actor)
    return tenant


@transaction.atomic
def reactivate(tenant, *, actor=None):
    """Lift a suspension. Access then follows the trial/paid dates as usual."""
    tenant = _locked(tenant)
    if tenant.status != Tenant.Status.SUSPENDED:
        raise SubscriptionError(_("This pharmacy is not suspended."))
    # Never-paid trials go back to their trial dates; paid and complimentary accounts to active.
    on_trial = tenant.paid_until is None and tenant.trial_ends_at is not None
    tenant.status = Tenant.Status.TRIAL if on_trial else Tenant.Status.ACTIVE
    tenant.suspended_reason = ""
    tenant.save(update_fields=["status", "suspended_reason", "updated_at"])
    _log(tenant, TenantEvent.Kind.REACTIVATED, _("Reactivated"), actor)
    return tenant


def subscription_banner(tenant, now=None):
    """A short notice for the top of every page, or None when there's nothing to say."""
    now = now or timezone.now()
    state = tenant.access_state(now)
    days = tenant.days_left(now)
    if state == AccessState.SUSPENDED:
        return {"level": "danger", "text": _("Your account is suspended. Contact ThingBD to restore access.")}
    if state == AccessState.EXPIRED:
        text = _("Your free trial has ended.") if tenant.status == Tenant.Status.TRIAL else _("Your subscription has expired.")
        return {"level": "danger", "text": text}
    if state == AccessState.GRACE:
        return {"level": "warning", "text": _(
            "Your subscription ended on %(end)s. Renew within %(days)s day(s) to keep using the system."
        ) % {"end": tenant.paid_until.strftime("%d %b %Y"), "days": max(0, days + settings.SUBSCRIPTION_GRACE_DAYS)}}
    if state == AccessState.TRIAL and days is not None and days <= 3:
        when = _("today") if days <= 0 else (_("tomorrow") if days == 1 else _("in %(days)s days") % {"days": days})
        return {"level": "info", "text": _("Your free trial ends %(when)s.") % {"when": when}}
    if state == AccessState.ACTIVE and days is not None and days <= 7:
        return {"level": "info", "text": _("Your subscription is paid until %(end)s.") % {"end": tenant.paid_until.strftime("%d %b %Y")}}
    return None
