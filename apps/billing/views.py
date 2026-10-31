from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .limits import usage_summary
from .models import Plan


@login_required
def subscription(request):
    tenant = request.tenant
    if tenant is None:
        return redirect("platform:tenants")
    return render(request, "billing/subscription.html", {
        "tenant": tenant,
        "state": tenant.access_state(),
        "usage": usage_summary(tenant),
        "payments": tenant.payments.select_related("plan")[:24],
        "plans": Plan.objects.filter(is_active=True, is_public=True),
        "payment_instructions": settings.SUBSCRIPTION_PAYMENT_INSTRUCTIONS,
    })
