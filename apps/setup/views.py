from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.people.access import has_perm, shop_permission_required

from .forms import BusinessProfileForm, BusinessSettingsForm, InvoiceNumberingForm
from .services import format_money, get_business_settings, invoice_sequence


@shop_permission_required("settings.show")
def business_settings(request):
    tenant = request.tenant
    settings = get_business_settings(tenant)
    sequence = invoice_sequence(tenant)
    can_edit = has_perm(request.user, "settings.edit")
    data = request.POST if request.method == "POST" else None
    files = request.FILES if request.method == "POST" else None
    profile = BusinessProfileForm(data, instance=tenant, prefix="profile")
    shop = BusinessSettingsForm(data, files, instance=settings, prefix="shop")
    numbering = InvoiceNumberingForm(data, instance=sequence, prefix="invoice")

    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if profile.is_valid() and shop.is_valid() and numbering.is_valid():
            profile.save()
            shop.save()
            numbering.save()
            messages.success(request, _("Business settings saved."))
            return redirect("setup:settings")

    if not can_edit:
        for form in (profile, shop, numbering):
            for field in form.fields.values():
                field.disabled = True

    return render(request, "setup/settings.html", {
        "profile": profile,
        "shop": shop,
        "numbering": numbering,
        "can_edit": can_edit,
        "settings_obj": settings,
        "examples": {
            "invoice": sequence.format(sequence.next_value),
            "money": format_money(1234.5, settings),
        },
    })
