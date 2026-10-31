from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from .forms import SignupForm
from .services import register_pharmacy


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        _tenant, owner = register_pharmacy(
            business_name=data["business_name"],
            business_phone=data["business_phone"],
            address=data["address"],
            plan=data["plan"],
            owner_name=data["owner_name"],
            email=data["email"],
            phone=data["phone"],
            password=data["password1"],
        )
        login(request, owner, backend="apps.accounts.backends.EmailOrPhoneBackend")
        messages.success(request, _("Welcome! Your %(days)s-day free trial has started.") % {"days": settings.TRIAL_DAYS})
        return redirect("core:dashboard")
    return render(request, "tenants/signup.html", {
        "form": form,
        "plans": form.fields["plan"].queryset,
        "trial_days": settings.TRIAL_DAYS,
    })
