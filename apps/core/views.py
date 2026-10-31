from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.tenants.models import Tenant


@login_required
def dashboard(request):
    context = {}
    if request.user.is_platform_admin:
        tenants = Tenant.objects.all()
        context["tenant_counts"] = {
            "total": tenants.count(),
            "active": tenants.filter(status=Tenant.Status.ACTIVE).count(),
            "trial": tenants.filter(status=Tenant.Status.TRIAL).count(),
            "suspended": tenants.filter(status=Tenant.Status.SUSPENDED).count(),
        }
    return render(request, "core/dashboard.html", context)


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
