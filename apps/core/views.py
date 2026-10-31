from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.console.services import platform_overview


@login_required
def dashboard(request):
    context = {}
    if request.user.is_platform_admin:
        context["overview"] = platform_overview()
    return render(request, "core/dashboard.html", context)


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
