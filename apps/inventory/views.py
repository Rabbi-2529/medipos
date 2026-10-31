import json

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.items.models import Medicine
from apps.outlets.models import Outlet
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, format_shop_date, get_business_settings
from apps.stock.models import BatchStock
from apps.stock.services import format_quantity

from .forms import StockCountForm, TransferForm, WriteOffForm
from .models import StockCount, Transfer, WriteOff
from .services import (
    InventoryError, cancel_transfer, clean_count_lines, clean_out_lines, create_write_off, dispatch_transfer, receive_transfer,
    save_stock_count, write_off_expired,
)


def _allowed_ids(request):
    return [o.pk for o in request.allowed_outlets]


def _chosen_outlet_ids(request):
    allowed = _allowed_ids(request)
    chosen = request.GET.get("outlet", "")
    return [int(chosen)] if chosen.isdigit() and int(chosen) in allowed else allowed


def _posted_json(request, name):
    try:
        data = json.loads(request.POST.get(name) or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _allowed_outlet(request, pk):
    if str(pk).isdigit() and int(pk) in _allowed_ids(request):
        return Outlet.objects.get(pk=pk)
    raise Http404


# ---- Shared: a medicine's batches at an outlet (the line editors ask for these) ---------

@shop_permission_required(("damage.create", "transfer.create", "adjustment.create"))
def batches(request):
    outlet = _allowed_outlet(request, request.GET.get("outlet", ""))
    medicine = get_object_or_404(Medicine.objects.select_related("sale_unit"),
                                 pk=request.GET.get("medicine") if str(request.GET.get("medicine", "")).isdigit() else 0)
    today = timezone.localdate()
    settings = get_business_settings(request.tenant)
    stocks = (BatchStock.objects.filter(outlet=outlet, medicine=medicine).select_related("batch")
              .order_by("batch__expiry_date", "batch__id"))
    return JsonResponse({
        "medicine": {"id": medicine.pk, "name": medicine.display_name, "unit": medicine.sale_unit.name, "trackExpiry": medicine.track_expiry},
        "batches": [{
            "id": s.batch_id, "batchNo": s.batch.batch_no or "—",
            "expiry": format_shop_date(s.batch.expiry_date, settings) if s.batch.expiry_date else "",
            "expired": bool(s.batch.expiry_date and s.batch.expiry_date <= today),
            "onHand": s.quantity, "unitCost": str(s.batch.unit_cost),
        } for s in stocks],
    })


def lines_texts():
    """Words the line editor (static/js/stock_lines.js) shows."""
    return {
        "qty": _("Quantity"), "counted": _("Counted"), "batchNo": _("Batch no."), "expiry": _("Expiry"), "expired": _("Expired"),
        "remove": _("Remove"), "noStock": _("None of this medicine in stock at this outlet."),
        "addBatch": _("Add a batch that isn't listed"), "pickOutlet": _("Pick the outlet first."),
        "changeOutlet": _("Changing the outlet clears the quantities you entered. Continue?"),
        "outSummary": _("%(units)s units, worth %(value)s at cost"),
        "countSummary": _("Difference: %(units)s units, %(value)s at cost"),
        "error": _("Something went wrong. Please try again."),
    }


def _lines_editor_context(request, mode, posted_lines=None, posted_new=None):
    return {
        "mode": mode,
        "initial_lines": posted_lines or [],
        "initial_new": posted_new or [],
        "texts": lines_texts(),
        "currency": get_business_settings(request.tenant).currency_symbol,
    }


def _prefill(request):
    """?outlet=…&batch=…[&qty=…] from the expiring-stock report: one line ready to go."""
    batch_id, outlet_id = request.GET.get("batch", ""), request.GET.get("outlet", "")
    if not (batch_id.isdigit() and outlet_id.isdigit() and int(outlet_id) in _allowed_ids(request)):
        return []
    stock = BatchStock.objects.filter(outlet_id=outlet_id, batch_id=batch_id).select_related("medicine").first()
    if stock is None or stock.quantity <= 0:
        return []
    return [{"medicine": stock.medicine_id, "name": stock.medicine.display_name, "batch": stock.batch_id, "qty": stock.quantity}]


# ---- Damage & expired write-offs -----------------------------------------------------

@shop_permission_required("damage.list")
def write_off_list(request):
    return render(request, "inventory/write_off_list.html", {"outlets": request.allowed_outlets, "reasons": WriteOff.Reason.choices})


@shop_permission_required("damage.list")
def write_off_data(request):
    settings = get_business_settings(request.tenant)
    queryset = WriteOff.objects.filter(outlet_id__in=_chosen_outlet_ids(request)).select_related("outlet", "created_by")
    if request.GET.get("reason") in WriteOff.Reason.values:
        queryset = queryset.filter(reason=request.GET["reason"])

    def row(w):
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', reverse("inventory:damage", args=[w.pk]), w.number),
            format_shop_date(w.date, settings), w.outlet.name,
            format_html('<span class="state-badge state-{}">{}</span>', "expired" if w.reason == "expired" else "grace", w.get_reason_display()),
            w.note or "—", str(w.created_by or "—"), format_money(w.total_cost, settings),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"),
                          order_columns=["number", "date", "outlet__name", "reason", None, None, "total_cost"], row=row,
                          search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(note__icontains=t)))


@shop_permission_required("damage.create")
def write_off_create(request):
    form = WriteOffForm(request.POST or None, outlets=request.allowed_outlets,
                        initial={"outlet": request.GET.get("outlet") or getattr(request.outlet, "pk", None),
                                 "reason": request.GET.get("reason") or WriteOff.Reason.DAMAGED})
    posted = _posted_json(request, "lines") if request.method == "POST" else _prefill(request)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            lines = clean_out_lines(data["outlet"], posted)
            write_off = create_write_off(tenant=request.tenant, outlet=data["outlet"], date=data["date"], reason=data["reason"],
                                         lines=lines, note=data["note"], user=request.user)
        except InventoryError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("%(number)s saved: %(value)s written off.") % {
                "number": write_off.number, "value": format_money(write_off.total_cost, get_business_settings(request.tenant))})
            return redirect("inventory:damage", pk=write_off.pk)
    return render(request, "inventory/write_off_form.html", {"form": form, **_lines_editor_context(request, "out", posted)})


@shop_permission_required("damage.create")
@require_POST
def write_off_expired_view(request):
    outlet = _allowed_outlet(request, request.POST.get("outlet", ""))
    try:
        write_off = write_off_expired(tenant=request.tenant, outlet=outlet, user=request.user)
    except InventoryError as error:
        messages.error(request, str(error))
        return redirect(reverse("stock:expiring") + f"?outlet={outlet.pk}")
    messages.success(request, _("%(number)s saved: all expired stock at %(outlet)s written off (%(value)s).") % {
        "number": write_off.number, "outlet": outlet.name, "value": format_money(write_off.total_cost, get_business_settings(request.tenant))})
    return redirect("inventory:damage", pk=write_off.pk)


@shop_permission_required("damage.list")
def write_off_detail(request, pk):
    write_off = get_object_or_404(WriteOff.objects.filter(outlet_id__in=_allowed_ids(request)).select_related("outlet", "created_by"), pk=pk)
    lines = list(write_off.lines.select_related("medicine", "batch", "medicine__sale_unit", "medicine__purchase_unit"))
    for line in lines:
        line.quantity_text = format_quantity(line.quantity, line.medicine)
    return render(request, "inventory/write_off_detail.html", {"write_off": write_off, "lines": lines})


# ---- Transfers ---------------------------------------------------------------------------

def _transfers(request):
    allowed = _allowed_ids(request)
    return Transfer.objects.filter(Q(from_outlet_id__in=allowed) | Q(to_outlet_id__in=allowed))


@shop_permission_required("transfer.list")
def transfer_list(request):
    allowed = _allowed_ids(request)
    incoming = Transfer.objects.filter(to_outlet_id__in=allowed, status=Transfer.Status.IN_TRANSIT).count()
    return render(request, "inventory/transfer_list.html", {"statuses": Transfer.Status.choices, "incoming": incoming})


@shop_permission_required("transfer.list")
def transfer_data(request):
    settings = get_business_settings(request.tenant)
    allowed = _allowed_ids(request)
    queryset = _transfers(request).select_related("from_outlet", "to_outlet")
    if request.GET.get("status") in Transfer.Status.values:
        queryset = queryset.filter(status=request.GET["status"])
    direction = request.GET.get("direction")
    if direction == "in":
        queryset = queryset.filter(to_outlet_id__in=allowed)
    elif direction == "out":
        queryset = queryset.filter(from_outlet_id__in=allowed)
    tones = {"in_transit": "grace", "received": "active", "cancelled": "suspended"}

    def row(t):
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', reverse("inventory:transfer", args=[t.pk]), t.number),
            timezone.localtime(t.dispatched_at).strftime("%d %b %Y, %I:%M %p"),
            t.from_outlet.name, t.to_outlet.name,
            format_html('<span class="state-badge state-{}">{}</span>', tones[t.status], t.get_status_display()),
            format_money(t.total_cost, settings),
        ]

    return datatable_json(request, queryset.order_by("-dispatched_at", "-id"),
                          order_columns=["number", "dispatched_at", "from_outlet__name", "to_outlet__name", "status", "total_cost"],
                          row=row, search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(note__icontains=t)))


@shop_permission_required("transfer.create")
def transfer_create(request):
    form = TransferForm(request.POST or None, outlets=request.allowed_outlets,
                        initial={"outlet": request.GET.get("outlet") or getattr(request.outlet, "pk", None)})
    posted = _posted_json(request, "lines") if request.method == "POST" else _prefill(request)
    if Outlet.objects.filter(is_active=True).count() < 2:
        messages.info(request, _("Transfers need at least two outlets. Add another outlet first."))
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            lines = clean_out_lines(data["outlet"], posted)
            transfer = dispatch_transfer(tenant=request.tenant, from_outlet=data["outlet"], to_outlet=data["to_outlet"],
                                         lines=lines, note=data["note"], user=request.user)
        except InventoryError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("%(number)s sent to %(outlet)s. It arrives there when they receive it.") % {
                "number": transfer.number, "outlet": transfer.to_outlet.name})
            return redirect("inventory:transfer", pk=transfer.pk)
    return render(request, "inventory/transfer_form.html", {"form": form, **_lines_editor_context(request, "out", posted)})


@shop_permission_required("transfer.list")
def transfer_detail(request, pk):
    transfer = get_object_or_404(_transfers(request).select_related("from_outlet", "to_outlet", "dispatched_by", "closed_by"), pk=pk)
    lines = list(transfer.lines.select_related("medicine", "batch", "medicine__sale_unit", "medicine__purchase_unit"))
    for line in lines:
        line.quantity_text = format_quantity(line.quantity, line.medicine)
    allowed = _allowed_ids(request)
    can_act = transfer.in_transit and has_perm(request.user, "transfer.create")
    return render(request, "inventory/transfer_detail.html", {
        "transfer": transfer, "lines": lines,
        "can_receive": can_act and transfer.to_outlet_id in allowed,
        "can_cancel": can_act and transfer.from_outlet_id in allowed,
    })


@shop_permission_required("transfer.create")
@require_POST
def transfer_receive(request, pk):
    transfer = get_object_or_404(Transfer.objects.filter(to_outlet_id__in=_allowed_ids(request)), pk=pk)
    try:
        receive_transfer(transfer, user=request.user)
    except InventoryError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("%(number)s received: the stock is now at %(outlet)s.") % {
            "number": transfer.number, "outlet": transfer.to_outlet.name})
    return redirect("inventory:transfer", pk=pk)


@shop_permission_required("transfer.create")
@require_POST
def transfer_cancel(request, pk):
    transfer = get_object_or_404(Transfer.objects.filter(from_outlet_id__in=_allowed_ids(request)), pk=pk)
    try:
        cancel_transfer(transfer, user=request.user)
    except InventoryError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("%(number)s cancelled: the stock is back at %(outlet)s.") % {
            "number": transfer.number, "outlet": transfer.from_outlet.name})
    return redirect("inventory:transfer", pk=pk)


# ---- Stock counts --------------------------------------------------------------------------

@shop_permission_required("adjustment.list")
def count_list(request):
    return render(request, "inventory/count_list.html", {"outlets": request.allowed_outlets})


@shop_permission_required("adjustment.list")
def count_data(request):
    settings = get_business_settings(request.tenant)
    queryset = StockCount.objects.filter(outlet_id__in=_chosen_outlet_ids(request)).select_related("outlet", "created_by")

    def row(c):
        net = c.net_cost
        return [
            format_html('<a class="fw-semibold" href="{}"><code>{}</code></a>', reverse("inventory:count", args=[c.pk]), c.number),
            format_shop_date(c.date, settings), c.outlet.name, c.note or "—", str(c.created_by or "—"),
            format_html('<span class="text-success">+{}</span>', format_money(c.gain_cost, settings)) if c.gain_cost else "—",
            format_html('<span class="text-danger">−{}</span>', format_money(c.loss_cost, settings)) if c.loss_cost else "—",
            format_html('<span class="fw-semibold {}">{}</span>', "text-danger" if net < 0 else "", format_money(net, settings)),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"),
                          order_columns=["number", "date", "outlet__name", None, None, "gain_cost", "loss_cost", None], row=row,
                          search=lambda qs, t: qs.filter(Q(number__icontains=t) | Q(note__icontains=t)))


@shop_permission_required("adjustment.create")
def count_create(request):
    form = StockCountForm(request.POST or None, outlets=request.allowed_outlets,
                          initial={"outlet": getattr(request.outlet, "pk", None)})
    posted = _posted_json(request, "lines") if request.method == "POST" else []
    posted_new = _posted_json(request, "new_batches") if request.method == "POST" else []
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            lines = clean_count_lines(data["outlet"], posted, posted_new)
            count = save_stock_count(tenant=request.tenant, outlet=data["outlet"], date=data["date"], lines=lines,
                                     note=data["note"], user=request.user)
        except InventoryError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("%(number)s saved: stock now matches what you counted.") % {"number": count.number})
            return redirect("inventory:count", pk=count.pk)
    return render(request, "inventory/count_form.html", {"form": form, **_lines_editor_context(request, "count", posted, posted_new)})


@shop_permission_required("adjustment.list")
def count_detail(request, pk):
    count = get_object_or_404(StockCount.objects.filter(outlet_id__in=_allowed_ids(request)).select_related("outlet", "created_by"), pk=pk)
    lines = list(count.lines.select_related("medicine", "batch"))
    return render(request, "inventory/count_detail.html", {"count": count, "lines": lines})
