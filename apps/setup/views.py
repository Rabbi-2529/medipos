from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.people.access import has_perm, shop_permission_required

from .forms import SHOW_FIELDS, BusinessProfileForm, BusinessSettingsForm, InvoiceDesignForm, InvoiceNumberingForm
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


@shop_permission_required("settings.show")
def invoice_settings(request):
    """Invoice design, the language invoices print in, and POS printing, with a live sample."""
    settings = get_business_settings(request.tenant)
    can_edit = has_perm(request.user, "settings.edit")
    form = InvoiceDesignForm(request.POST or None, request.FILES or None, instance=settings)
    size = request.GET.get("size") if request.GET.get("size") in ("58", "80", "a4") else settings.receipt_size
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if request.POST.get("action") == "remove_letterhead":
            settings.letterhead.delete(save=True)
            messages.success(request, _("Letterhead removed."))
            return redirect(f"{reverse('setup:invoice')}?size=a4")
        if form.is_valid():
            form.save()
            messages.success(request, _("Invoice settings saved."))
            return redirect(f"{reverse('setup:invoice')}?size={request.POST.get('preview_size') or size}")
    if not can_edit:
        for field in form.fields.values():
            field.disabled = True
    return render(request, "setup/invoice_settings.html", {
        "form": form, "can_edit": can_edit, "settings_obj": settings, "size": size, "show_fields": [form[name] for name in SHOW_FIELDS],
    })


@xframe_options_sameorigin  # shown in a frame on the settings page
@shop_permission_required("settings.show")
def invoice_preview(request):
    """A made-up bill printed with the saved settings (the settings page shows it in a frame)."""
    from apps.sales.views import render_receipt

    sale, context = sample_bill(request.tenant)
    return render_receipt(request, sale, size=request.GET.get("size"), preview=True, context=context)


def sample_bill(tenant):
    """A sale that shows every part of an invoice: a customer, a discount, some due, points, batches."""
    from types import SimpleNamespace as Row

    from apps.outlets.models import Outlet

    D = Decimal
    outlet = Outlet.objects.filter(is_active=True).order_by("pk").first() or Row(name=_("Main branch"), address="", phone=tenant.phone)
    strip, piece = _("Strip"), _("Piece")
    lines = [
        Row(medicine=Row(display_name="Napa 500 mg Tablet", generic=Row(name="Paracetamol")), unit_qty=2, unit_label=strip, quantity=20,
            unit_price=D("1.20"), discount=D("0"), line_total=D("24.00"), batch_note="NP2041 · 08/2028"),
        Row(medicine=Row(display_name="Seclo 20 mg Capsule", generic=Row(name="Omeprazole")), unit_qty=1, unit_label=strip, quantity=10,
            unit_price=D("6.00"), discount=D("5.00"), line_total=D("55.00"), batch_note="SC1188 · 03/2027"),
        Row(medicine=Row(display_name="Ceevit 250 mg Tablet", generic=Row(name="Vitamin C")), unit_qty=10, unit_label=piece, quantity=10,
            unit_price=D("3.00"), discount=D("0"), line_total=D("30.00"), batch_note="CV0907 · 11/2027"),
    ]
    sale = Row(number=invoice_sequence(tenant).format(42), sold_at=timezone.now(), created_by="Sumi Akter",
               customer=Row(name="Rahim Uddin", phone="01711223344"), outlet=outlet, subtotal=D("114.00"), line_discount=D("5.00"),
               discount=D("9.00"), points_discount=D("0"), points_redeemed=0, points_earned=1, vat_amount=D("0"), vat_percent=D("0"),
               rounding=D("0"), total=D("100.00"), paid=D("60.00"), change=D("0"), cash_tendered=None, due=D("40.00"), rx_checked=True)
    return sale, {"sale": sale, "lines": lines, "payments": [Row(account=Row(name=_("Cash")), amount=D("60.00"))],
                  "total_discount": D("14.00"), "customer_due": D("140.00"), "points_after": 25}
