from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.core.shop_views import ShopCreateView, ShopUpdateView
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, get_business_settings

from .forms import PaymentForm, SupplierForm
from .models import Supplier, SupplierPayment
from .services import SupplierError, balance, ledger_with_running_balance, record_payment, set_opening_balance, with_balances

SUPPLIERS = reverse_lazy("suppliers:list")


@shop_permission_required("supplier.list")
def supplier_list(request):
    return render(request, "suppliers/supplier_list.html")


@shop_permission_required("supplier.list")
def supplier_data(request):
    settings = get_business_settings(request.tenant)
    queryset = with_balances(Supplier.objects.all()).annotate(purchase_count=Count("purchases", filter=Q(purchases__status="received")))
    if request.GET.get("status") in ("active", "inactive"):
        queryset = queryset.filter(is_active=request.GET["status"] == "active")
    if request.GET.get("due") == "yes":
        queryset = queryset.filter(balance__gt=0)

    def row(s):
        url = reverse("suppliers:detail", args=[s.pk])
        owe = format_html('<span class="{}">{}</span>', "text-danger fw-semibold" if s.balance > 0 else "", format_money(s.balance, settings))
        return [
            format_html('<a class="fw-semibold" href="{}">{}</a><small class="d-block text-body-secondary">{}</small>', url, s.name, s.contact_person),
            s.phone or "—",
            s.purchase_count,
            owe,
            format_html('<span class="state-badge state-{}">{}</span>', "active" if s.is_active else "suspended", _("Active") if s.is_active else _("Inactive")),
            format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>', url, _("Open")),
        ]

    return datatable_json(
        request, queryset.order_by("name"),
        order_columns=["name", "phone", "purchase_count", "balance", "is_active", None],
        row=row,
        search=lambda qs, t: qs.filter(Q(name__icontains=t) | Q(phone__icontains=t) | Q(contact_person__icontains=t)),
    )


class SupplierCreateView(ShopCreateView):
    permission = "supplier.create"
    form_class = SupplierForm
    title = gettext_lazy("Add supplier")
    success_url = SUPPLIERS

    def form_valid(self, form):
        response = super().form_valid(form)
        set_opening_balance(self.object)
        return response


class SupplierUpdateView(ShopUpdateView):
    permission = "supplier.edit"
    form_class = SupplierForm
    title = gettext_lazy("Edit supplier")

    def get_queryset(self):
        return Supplier.objects.all()

    def get_success_url(self):
        return reverse("suppliers:detail", args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        set_opening_balance(self.object)
        return response


@shop_permission_required("supplier.list")
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier.objects.all(), pk=pk)
    ledger = ledger_with_running_balance(supplier)
    return render(request, "suppliers/supplier_detail.html", {
        "supplier": supplier,
        "balance": balance(supplier),
        "ledger": list(reversed(ledger))[:200],
        "purchases": supplier.purchases.select_related("outlet").order_by("-date", "-id")[:20],
    })


@shop_permission_required("supplier.delete")
@require_POST
def supplier_toggle(request, pk):
    supplier = get_object_or_404(Supplier.objects.all(), pk=pk)
    supplier.is_active = not supplier.is_active
    supplier.save(update_fields=["is_active", "updated_at"])
    messages.success(request, (_("%(name)s activated.") if supplier.is_active else _("%(name)s deactivated.")) % {"name": supplier.name})
    return redirect("suppliers:detail", pk=pk)


@shop_permission_required("purchase.list")
def supplier_lookup(request):
    term = request.GET.get("q", "").strip()
    queryset = Supplier.objects.filter(is_active=True)
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    rows = list(queryset.order_by("name")[:31])
    return JsonResponse({"results": [{"id": s.pk, "text": s.name} for s in rows[:30]], "pagination": {"more": len(rows) > 30}})


# ---- Payments ------------------------------------------------------------------

@shop_permission_required("supplier_payment.list")
def payment_list(request):
    return render(request, "suppliers/payment_list.html")


@shop_permission_required("supplier_payment.list")
def payment_data(request):
    settings = get_business_settings(request.tenant)
    queryset = SupplierPayment.objects.select_related("supplier", "purchase", "account")

    def row(p):
        return [
            format_html("<code>{}</code>", p.number),
            p.date.strftime("%d %b %Y"),
            format_html('<a href="{}">{}</a>', reverse("suppliers:detail", args=[p.supplier_id]), p.supplier.name),
            format_html('<a href="{}">{}</a>', reverse("purchases:detail", args=[p.purchase_id]), p.purchase.number) if p.purchase else "—",
            p.account.name,
            p.reference or "—",
            format_money(p.amount, settings),
        ]

    return datatable_json(
        request, queryset.order_by("-date", "-id"),
        order_columns=["number", "date", "supplier__name", None, "account__name", None, "amount"],
        row=row,
        search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(supplier__name__icontains=t) | Q(reference__icontains=t)),
    )


@shop_permission_required("supplier_payment.create")
def payment_create(request):
    initial = {k: request.GET[k] for k in ("supplier", "purchase", "amount") if request.GET.get(k)}
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            payment = record_payment(supplier=data["supplier"], amount=data["amount"], account=data["account"], date=data["date"],
                                     purchase=data["purchase"], reference=data["reference"], note=data["note"], user=request.user)
        except SupplierError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Payment %(number)s saved.") % {"number": payment.number})
            if payment.purchase_id:
                return redirect("purchases:detail", pk=payment.purchase_id)
            return redirect("suppliers:detail", pk=payment.supplier_id)
    return render(request, "suppliers/payment_form.html", {"form": form, "can_list": has_perm(request.user, "supplier_payment.list")})
