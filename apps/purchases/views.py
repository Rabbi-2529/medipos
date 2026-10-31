import json
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, get_business_settings
from apps.stock.services import format_quantity
from apps.suppliers.models import Supplier, SupplierPayment
from apps.suppliers.services import SupplierError, balance, record_payment

from .forms import PurchaseForm, ReturnForm
from .models import Purchase, PurchaseReturn
from .services import PurchaseError, clean_lines, create_return, delete_draft, receive, returnable, save_draft

MONEY = DecimalField(max_digits=14, decimal_places=2)
ZERO = Value(Decimal("0"))


def with_amounts(queryset):
    """Annotate paid / returned / due without multiplying rows."""
    def total(model, link):
        rows = model.objects.filter(**{link: OuterRef("pk")}).order_by().values(link).annotate(t=Sum("amount" if model is SupplierPayment else "total")).values("t")
        return Coalesce(Subquery(rows, output_field=MONEY), ZERO, output_field=MONEY)

    return queryset.annotate(paid=total(SupplierPayment, "purchase"), returned=total(PurchaseReturn, "purchase")).annotate(
        due=Coalesce("total", ZERO, output_field=MONEY) - Coalesce("paid", ZERO, output_field=MONEY) - Coalesce("returned", ZERO, output_field=MONEY)
    )


def _allowed_outlet_ids(request):
    return [o.pk for o in request.allowed_outlets]


def _purchases(request):
    """Purchases at outlets this user may work in."""
    return Purchase.objects.filter(outlet_id__in=_allowed_outlet_ids(request))


# ---- List ----------------------------------------------------------------------

@shop_permission_required("purchase.list")
def purchase_list(request):
    return render(request, "purchases/purchase_list.html", {"outlets": request.allowed_outlets})


@shop_permission_required("purchase.list")
def purchase_data(request):
    settings = get_business_settings(request.tenant)
    queryset = with_amounts(_purchases(request).select_related("supplier", "outlet"))
    status = request.GET.get("status")
    if status in Purchase.Status.values:
        queryset = queryset.filter(status=status)
    elif status == "due":
        queryset = queryset.filter(status=Purchase.Status.RECEIVED, due__gt=0)
    if request.GET.get("outlet", "").isdigit():
        queryset = queryset.filter(outlet_id=request.GET["outlet"])
    if request.GET.get("supplier", "").isdigit():
        queryset = queryset.filter(supplier_id=request.GET["supplier"])

    def row(p):
        url = reverse("purchases:detail", args=[p.pk])
        state = ("grace", _("Draft")) if p.is_draft else (("active", _("Paid")) if p.due <= 0 else ("expired", _("Due")))
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', url, p.number),
            p.date.strftime("%d %b %Y"),
            format_html('{}<small class="d-block text-body-secondary">{}</small>', p.supplier.name, p.supplier_invoice_no),
            p.outlet.name,
            format_money(p.total, settings),
            format_money(p.paid, settings),
            format_money(p.due, settings) if not p.is_draft else "—",
            format_html('<span class="state-badge state-{}">{}</span>', *state),
        ]

    return datatable_json(
        request, queryset.order_by("-date", "-id"),
        order_columns=["number", "date", "supplier__name", "outlet__name", "total", "paid", "due", "status"],
        row=row,
        search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(supplier__name__icontains=t) | Q(supplier_invoice_no__icontains=t)),
    )


# ---- Create / edit ---------------------------------------------------------------

def _line_payload(line):
    medicine = line.medicine
    return {
        "medicine": medicine.pk, "text": medicine.display_name, "conversion": medicine.conversion,
        "sale_unit": medicine.sale_unit.name, "purchase_unit": medicine.purchase_unit.name, "track_expiry": medicine.track_expiry,
        "batch_no": line.batch_no, "expiry": line.expiry_date.strftime("%Y-%m") if line.expiry_date else "",
        "packs": line.packs, "units": line.units, "free_units": line.free_units,
        "pack_cost": str(line.pack_cost.normalize()), "sale_price": str(line.sale_price),
    }


def _posted_lines(form):
    try:
        raw = json.loads(form.data.get("lines_json") or "[]")
    except json.JSONDecodeError:
        raw = []
    return raw if isinstance(raw, list) else []


def _purchase_form(request, purchase=None):
    initial = {}
    if purchase:
        initial = {"supplier": purchase.supplier_id, "outlet": purchase.outlet_id, "date": purchase.date,
                   "supplier_invoice_no": purchase.supplier_invoice_no, "discount": purchase.discount,
                   "update_prices": purchase.update_prices, "note": purchase.note}
    else:
        initial = {"outlet": getattr(request.outlet, "pk", None), "supplier": request.GET.get("supplier")}
    form = PurchaseForm(request.POST or None, initial=initial, allowed_outlets=request.allowed_outlets)
    lines = [_line_payload(line) for line in purchase.lines.select_related("medicine__sale_unit", "medicine__purchase_unit")] if purchase else []

    if request.method == "POST":
        lines = _posted_lines(form)
        receive_now = "receive" in request.POST
        if receive_now and not has_perm(request.user, "purchase.create"):
            form.add_error(None, _("You aren't allowed to receive purchases."))
        elif form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    saved = save_draft(
                        tenant=request.tenant, purchase=purchase, outlet=data["outlet"], supplier=data["supplier"], date=data["date"],
                        lines=clean_lines(lines), supplier_invoice_no=data["supplier_invoice_no"], discount=data["discount"] or 0,
                        note=data["note"], update_prices=data["update_prices"], user=request.user,
                    )
                    if receive_now:
                        saved = receive(saved, user=request.user)  # fresh copy: status is now "received"
                        if (data["paid_now"] or 0) > 0:
                            record_payment(supplier=saved.supplier, amount=data["paid_now"], account=data["account"],
                                           date=saved.date, purchase=saved, user=request.user)
            except (PurchaseError, SupplierError) as error:
                form.add_error(None, str(error))
            else:
                text = _("%(number)s received: stock added at %(outlet)s.") if receive_now else _("%(number)s saved as a draft.")
                messages.success(request, text % {"number": saved.number, "outlet": saved.outlet.name})
                return redirect("purchases:detail", pk=saved.pk)

    return render(request, "purchases/purchase_form.html", {
        "form": form,
        "purchase": purchase,
        "lines": lines,
        "currency": get_business_settings(request.tenant).currency_symbol,
        "title": _("Edit %(number)s") % {"number": purchase.number} if purchase else _("New purchase"),
    })


@shop_permission_required("purchase.create")
def purchase_create(request):
    return _purchase_form(request)


@shop_permission_required("purchase.edit")
def purchase_edit(request, pk):
    purchase = get_object_or_404(_purchases(request), pk=pk)
    if not purchase.is_draft:
        messages.info(request, _("Received purchases can't be edited. Use a return to send items back."))
        return redirect("purchases:detail", pk=pk)
    return _purchase_form(request, purchase)


# ---- Detail & actions --------------------------------------------------------------

@shop_permission_required("purchase.list")
def purchase_detail(request, pk):
    purchase = get_object_or_404(with_amounts(_purchases(request).select_related("supplier", "outlet", "created_by", "received_by")), pk=pk)
    lines = list(purchase.lines.select_related("medicine__sale_unit", "medicine__purchase_unit", "batch"))
    for line in lines:
        line.quantity_text = format_quantity(line.paid_units, line.medicine)
    return render(request, "purchases/purchase_detail.html", {
        "purchase": purchase,
        "lines": lines,
        "payments": purchase.payments.select_related("account"),
        "returns": purchase.returns.all(),
    })


@shop_permission_required("purchase.create")
@require_POST
def purchase_receive(request, pk):
    purchase = get_object_or_404(_purchases(request), pk=pk)
    try:
        receive(purchase, user=request.user)
    except PurchaseError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("%(number)s received: stock added at %(outlet)s.") % {"number": purchase.number, "outlet": purchase.outlet.name})
    return redirect("purchases:detail", pk=pk)


@shop_permission_required("purchase.delete")
@require_POST
def purchase_delete(request, pk):
    purchase = get_object_or_404(_purchases(request), pk=pk)
    try:
        delete_draft(purchase)
    except PurchaseError as error:
        messages.error(request, str(error))
        return redirect("purchases:detail", pk=pk)
    messages.success(request, _("Draft deleted."))
    return redirect("purchases:list")


@shop_permission_required("supplier_payment.create")
def due_lookup(request):
    """Received purchases of a supplier that still have money due (for the payment form)."""
    supplier = Supplier.objects.filter(pk=request.GET.get("supplier") if str(request.GET.get("supplier", "")).isdigit() else None).first()
    if supplier is None:
        raise Http404
    settings = get_business_settings(request.tenant)
    rows = with_amounts(Purchase.objects.filter(supplier=supplier, status=Purchase.Status.RECEIVED)).filter(due__gt=0).order_by("date")
    owed = balance(supplier)
    return JsonResponse({
        "results": [{"id": p.pk, "text": f"{p.number} · {p.date:%d %b %Y} · due {format_money(p.due, settings)}",
                     "due": str(Decimal(p.due).quantize(Decimal("0.01")))} for p in rows[:100]],
        "balance_text": _("You owe %(name)s %(amount)s in total.") % {"name": supplier.name, "amount": format_money(owed, settings)},
    })


# ---- Returns ---------------------------------------------------------------------

@shop_permission_required("purchase_return.create")
def return_create(request, pk):
    purchase = get_object_or_404(_purchases(request).select_related("supplier", "outlet"), pk=pk)
    if purchase.is_draft:
        raise Http404
    rows = returnable(purchase)
    form = ReturnForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        quantities = {}
        for line, _left in rows:
            value = request.POST.get(f"qty_{line.pk}", "").strip()
            if value:
                try:
                    quantities[line.pk] = int(value)
                except ValueError:
                    form.add_error(None, _("Quantities must be whole numbers."))
                    break
        if not form.errors:
            try:
                purchase_return = create_return(purchase, quantities, date=form.cleaned_data["date"],
                                                reason=form.cleaned_data["reason"], user=request.user)
            except PurchaseError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("Return %(number)s saved: stock taken out, %(supplier)s balance reduced.") % {
                    "number": purchase_return.number, "supplier": purchase.supplier.name})
                return redirect("purchases:detail", pk=pk)
    from apps.stock.services import batch_on_hand

    picked_batch = request.GET.get("batch", "")
    lines = []
    for line, left in rows:
        on_hand = batch_on_hand(purchase.outlet, line.batch) if line.batch else 0
        posted = request.POST.get(f"qty_{line.pk}", "")
        if request.method == "GET" and picked_batch.isdigit() and line.batch_id == int(picked_batch):
            posted = min(left, on_hand) or ""  # sent here from the expiring-stock report
        lines.append({"line": line, "left": left, "on_hand": on_hand, "posted": posted})
    return render(request, "purchases/return_form.html", {"purchase": purchase, "lines": lines, "form": form})


@shop_permission_required("purchase_return.list")
def return_list(request):
    return render(request, "purchases/return_list.html")


@shop_permission_required("purchase_return.list")
def return_data(request):
    settings = get_business_settings(request.tenant)
    queryset = PurchaseReturn.objects.filter(outlet_id__in=_allowed_outlet_ids(request)).select_related("supplier", "purchase", "outlet")

    def row(r):
        return [
            format_html("<code>{}</code>", r.number),
            r.date.strftime("%d %b %Y"),
            r.supplier.name,
            format_html('<a href="{}">{}</a>', reverse("purchases:detail", args=[r.purchase_id]), r.purchase.number),
            r.outlet.name,
            r.reason or "—",
            format_money(r.total, settings),
        ]

    return datatable_json(
        request, queryset.order_by("-date", "-id"),
        order_columns=["number", "date", "supplier__name", "purchase__number", "outlet__name", None, "total"],
        row=row,
        search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(supplier__name__icontains=t) | Q(purchase__number__icontains=t)),
    )
