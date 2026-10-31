from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from apps.people.access import has_perm

from .limits import usage_summary
from .models import Plan


@login_required
def subscription(request):
    tenant = request.tenant
    if tenant is None:
        return redirect("platform:tenants")
    if not has_perm(request.user, "subscription.show"):
        if tenant.has_access:
            raise PermissionDenied
        # Locked-out staff land here; tell them why without showing billing details.
        return render(request, "billing/locked_notice.html")
    return render(request, "billing/subscription.html", {
        "tenant": tenant,
        "state": tenant.access_state(),
        "usage": usage_summary(tenant),
        "payments": tenant.payments.select_related("plan")[:24],
        "plans": Plan.objects.filter(is_active=True, is_public=True),
        "payment_instructions": settings.SUBSCRIPTION_PAYMENT_INSTRUCTIONS,
    })
