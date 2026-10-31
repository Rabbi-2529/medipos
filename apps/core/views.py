from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.console.services import platform_overview


def _setup_checklist(tenant):
    from apps.accounting.models import PaymentAccount
    from apps.outlets.models import Counter
    from apps.setup.services import get_business_settings

    settings = get_business_settings(tenant)
    return [
        {"label": _("Add your drug license number and logo"), "done": bool(settings.drug_license_no and settings.logo),
         "url": reverse("setup:settings")},
        {"label": _("Add counters (tills) to your outlets"), "done": Counter.objects.exists(),
         "url": reverse("outlets:counters")},
        {"label": _("Add your staff and give them roles"), "done": tenant.users.filter(is_active=True).count() > 1,
         "url": reverse("people:employees")},
        {"label": _("Add bKash/Nagad/bank account numbers"),
         "done": PaymentAccount.objects.exclude(kind=PaymentAccount.Kind.CASH).exclude(account_number="").exists(),
         "url": reverse("accounting:payment_accounts")},
    ]


@login_required
def dashboard(request):
    context = {}
    if request.user.is_platform_admin:
        context["overview"] = platform_overview()
    elif request.tenant is not None and request.user.is_owner:
        checklist = _setup_checklist(request.tenant)
        if not all(step["done"] for step in checklist):
            context["checklist"] = checklist
    return render(request, "core/dashboard.html", context)


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
