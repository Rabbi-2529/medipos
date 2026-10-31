import json
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Max, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.core.shop_views import ShopCreateView, ShopUpdateView
from apps.people.access import shop_permission_required
from apps.setup.services import format_money, get_business_settings

from .forms import CustomerForm, ReceiptForm
from .models import Customer, CustomerReceipt
from .services import CustomerError, balance, collect, ledger_with_running_balance, sale_due, set_opening_balance, with_balances

CUSTOMERS = reverse_lazy("customers:list")


@shop_permission_required("customer.list")
def customer_list(request):
    return render(request, "customers/customer_list.html")


@shop_permission_required("customer.list")
def customer_data(request):
    settings = get_business_settings(request.tenant)
    queryset = with_balances(Customer.objects.all()).annotate(sale_count=Count("sales"), last_sale=Max("sales__sold_at"))
    if request.GET.get("due") == "yes":
        queryset = queryset.filter(balance__gt=0)
    if request.GET.get("status") in ("active", "inactive"):
        queryset = queryset.filter(is_active=request.GET["status"] == "active")

    def row(c):
        url = reverse("customers:detail", args=[c.pk])
        return [
            format_html('<a class="fw-semibold" href="{}">{}</a><small class="d-block text-body-secondary">{}</small>', url, c.name, c.phone),
            c.sale_count,
            c.last_sale.strftime("%d %b %Y") if c.last_sale else "—",
            format_html('<span class="{}">{}</span>', "text-danger fw-semibold" if c.balance > 0 else "", format_money(c.balance, settings)),
            format_money(c.credit_limit, settings) if c.credit_limit is not None else _("No limit"),
            format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>', url, _("Open")),
        ]

    return datatable_json(
        request, queryset.order_by("name"),
        order_columns=["name", "sale_count", "last_sale", "balance", "credit_limit", None],
        row=row, search=lambda qs, t: qs.filter(Q(name__icontains=t) | Q(phone__icontains=t)),
    )


class CustomerCreateView(ShopCreateView):
    permission = "customer.create"
    form_class = CustomerForm
    title = gettext_lazy("Add customer")
    success_url = CUSTOMERS

    def form_valid(self, form):
        response = super().form_valid(form)
        set_opening_balance(self.object)
        return response


class CustomerUpdateView(ShopUpdateView):
    permission = "customer.edit"
    form_class = CustomerForm
    title = gettext_lazy("Edit customer")

    def get_queryset(self):
        return Customer.objects.all()

    def get_success_url(self):
        return reverse("customers:detail", args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        set_opening_balance(self.object)
        return response


@shop_permission_required("customer.list")
def customer_detail(request, pk):
    customer = get_object_or_404(Customer.objects.all(), pk=pk)
    return render(request, "customers/customer_detail.html", {
        "customer": customer,
        "balance": balance(customer),
        "ledger": list(reversed(ledger_with_running_balance(customer)))[:200],
        "sales": customer.sales.select_related("outlet").order_by("-sold_at")[:20],
    })


@shop_permission_required("customer.delete")
@require_POST
def customer_toggle(request, pk):
    customer = get_object_or_404(Customer.objects.all(), pk=pk)
    customer.is_active = not customer.is_active
    customer.save(update_fields=["is_active", "updated_at"])
    messages.success(request, (_("%(name)s activated.") if customer.is_active else _("%(name)s deactivated.")) % {"name": customer.name})
    return redirect("customers:detail", pk=pk)


def _amount(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def _lookup_row(customer, owed=None):
    return {"id": customer.pk, "text": str(customer), "name": customer.name, "phone": customer.phone,
            "balance": _amount(owed if owed is not None else balance(customer)),
            "credit_limit": None if customer.credit_limit is None else _amount(customer.credit_limit),
            "default_discount": _amount(customer.default_discount)}


@shop_permission_required("customer.list")
def customer_lookup(request):
    term = request.GET.get("q", "").strip()
    queryset = with_balances(Customer.objects.filter(is_active=True))
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    rows = list(queryset.order_by("name")[:31])
    return JsonResponse({"results": [_lookup_row(c, c.balance) for c in rows[:30]], "pagination": {"more": len(rows) > 30}})


@shop_permission_required("customer.create")
@require_POST
def customer_quick_create(request):
    """POS "new customer" box: name + phone (+ credit limit). JSON in, JSON out."""
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        data = {}
    form = CustomerForm({"name": data.get("name", ""), "phone": data.get("phone", ""), "email": "", "address": "",
                         "credit_limit": data.get("credit_limit", ""), "default_discount": "0", "opening_balance": "0", "notes": ""})
    if not form.is_valid():
        errors = [e for errs in form.errors.values() for e in errs]
        return JsonResponse({"error": " ".join(errors)}, status=400)
    return JsonResponse(_lookup_row(form.save(), owed=0))


# ---- Due collection ------------------------------------------------------------

@shop_permission_required("customer_receive.list")
def receipt_list(request):
    return render(request, "customers/receipt_list.html")


@shop_permission_required("customer_receive.list")
def receipt_data(request):
    settings = get_business_settings(request.tenant)
    queryset = CustomerReceipt.objects.select_related("customer", "sale", "account")

    def row(r):
        return [
            format_html("<code>{}</code>", r.number), r.date.strftime("%d %b %Y"),
            format_html('<a href="{}">{}</a>', reverse("customers:detail", args=[r.customer_id]), r.customer.name),
            format_html('<a href="{}">{}</a>', reverse("sales:detail", args=[r.sale_id]), r.sale.number) if r.sale else "—",
            r.account.name, r.reference or "—", format_money(r.amount, settings),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"),
                          order_columns=["number", "date", "customer__name", None, "account__name", None, "amount"], row=row,
                          search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(customer__name__icontains=t) | Q(customer__phone__icontains=t)))


@shop_permission_required("customer_receive.create")
def receipt_create(request):
    initial = {k: request.GET[k] for k in ("customer", "sale", "amount") if request.GET.get(k)}
    form = ReceiptForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        from apps.sales.services import open_register

        try:
            receipt = collect(data["customer"], amount=data["amount"], account=data["account"], date=data["date"], sale=data["sale"],
                              outlet=request.outlet, register=open_register(request.user, request.outlet),
                              reference=data["reference"], note=data["note"], user=request.user)
        except CustomerError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("%(number)s: %(amount)s collected from %(name)s.") % {
                "number": receipt.number, "amount": format_money(receipt.amount, get_business_settings(request.tenant)), "name": receipt.customer.name})
            return redirect("customers:detail", pk=receipt.customer_id)
    return render(request, "customers/receipt_form.html", {"form": form})


@shop_permission_required("customer_receive.create")
def due_sales(request):
    customer = Customer.objects.filter(pk=request.GET.get("customer") if str(request.GET.get("customer", "")).isdigit() else None).first()
    if customer is None:
        raise Http404
    settings = get_business_settings(request.tenant)
    rows = []
    for sale in customer.sales.order_by("sold_at"):
        due = sale_due(sale)
        if due > 0:
            rows.append({"id": sale.pk, "text": f"{sale.number} · {sale.sold_at:%d %b %Y} · due {format_money(due, settings)}", "due": str(due)})
    return JsonResponse({"results": rows, "balance_text": _("%(name)s owes %(amount)s in total.") % {
        "name": customer.name, "amount": format_money(balance(customer), settings)}})
