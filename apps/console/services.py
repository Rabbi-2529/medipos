from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import SubscriptionPayment
from apps.tenants.models import AccessState, Tenant


def platform_overview(now=None):
    now = now or timezone.now()
    tenants = Tenant.objects.all()
    counts = {state.value: tenants.in_state(state, now).count() for state in AccessState}
    month_start = timezone.localdate(now).replace(day=1)
    revenue = SubscriptionPayment.objects.filter(paid_at__date__gte=month_start).aggregate(total=Sum("amount"))["total"]
    expiring = sorted(tenants.expiring_within(7, now).select_related("plan"), key=lambda t: t.ends_on())
    return {
        "total": tenants.count(),
        "counts": counts,
        "attention": counts[AccessState.GRACE] + counts[AccessState.EXPIRED],
        "revenue_this_month": revenue or 0,
        "expiring": expiring[:8],
        "recent": tenants.select_related("plan").order_by("-created_at")[:6],
    }
