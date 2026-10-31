from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.db.models import Count, DateField, DecimalField, F, IntegerField, Min, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core import staging
from apps.core.datatables import datatable_json
from apps.core.forms import BootstrapFormMixin
from apps.core.spreadsheets import SpreadsheetError, xlsx_response
from apps.items.models import Medicine
from apps.items.services import search_medicines
from apps.outlets.models import Outlet
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, format_shop_date, get_business_settings

from . import opening
from .models import BatchStock, StockMovement
from .services import format_quantity

MONEY = DecimalField(max_digits=16, decimal_places=4)
SOON_DAYS = 90


def _outlet_ids(request):
    """The outlet chosen in the filter (if allowed), else every outlet this user may see."""
    allowed = [o.pk for o in request.allowed_outlets]
    chosen = request.GET.get("outlet", "")
    return [int(chosen)] if chosen.isdigit() and int(chosen) in allowed else allowed


def stock_annotations(queryset, outlet_ids):
    rows = BatchStock.objects.filter(medicine=OuterRef("pk"), outlet_id__in=outlet_ids, quantity__gt=0).order_by().values("medicine")

    def sub(expr, field):
        return Subquery(rows.annotate(x=expr).values("x"), output_field=field)

    return queryset.annotate(
        on_hand=Coalesce(sub(Sum("quantity"), IntegerField()), Value(0)),
        batch_count=Coalesce(sub(Count("id"), IntegerField()), Value(0)),
        nearest_expiry=sub(Min("batch__expiry_date"), DateField()),
        cost_value=Coalesce(sub(Sum(F("quantity") * F("batch__unit_cost"), output_field=MONEY), MONEY), Value(Decimal("0")), output_field=MONEY),
    )


@shop_permission_required("stock.list")
def stock_list(request):
    return render(request, "stock/stock_list.html", {
        "outlets": request.allowed_outlets,
        "current": getattr(request.outlet, "pk", None),
    })


@shop_permission_required("stock.list")
def stock_data(request):
    settings = get_business_settings(request.tenant)
    outlet_ids = _outlet_ids(request)
    queryset = stock_annotations(Medicine.objects.filter(is_active=True).select_related("generic", "category", "sale_unit", "purchase_unit"), outlet_ids)
    if request.GET.get("show", "in_stock") == "in_stock":
        queryset = queryset.filter(on_hand__gt=0)
    if request.GET.get("category"):
        queryset = queryset.filter(category__name=request.GET["category"])
    soon = timezone.localdate() + timedelta(days=SOON_DAYS)

    def row(m):
        expiry = ""
        if m.nearest_expiry:
            tone = "text-danger fw-semibold" if m.nearest_expiry <= soon else ""
            expiry = format_html('<span class="{}">{}</span>', tone, format_shop_date(m.nearest_expiry, settings))
        return [
            format_html("<code>{}</code>", m.code),
            format_html('<a class="fw-semibold" href="{}">{}</a><small class="d-block text-body-secondary">{}</small>',
                        reverse("stock:card", args=[m.pk]), m.display_name, m.generic.name if m.generic else ""),
            m.category.name if m.category else "—",
            format_html('<span class="fw-semibold">{}</span>', format_quantity(m.on_hand, m)),
            m.batch_count,
            expiry or "—",
            format_money(m.cost_value, settings),
            format_money(m.on_hand * m.sale_price, settings),
        ]

    totals = BatchStock.objects.filter(outlet_id__in=outlet_ids, quantity__gt=0).aggregate(
        cost=Sum(F("quantity") * F("batch__unit_cost"), output_field=MONEY),
        mrp=Sum(F("quantity") * F("medicine__sale_price"), output_field=MONEY),
        items=Count("medicine", distinct=True),
    )
    return datatable_json(
        request, queryset.order_by("name", "strength"),
        order_columns=["code", "name", "category__name", "on_hand", "batch_count", "nearest_expiry", "cost_value", None],
        row=row, search=search_medicines,
        extra={"summary": {
            "items": totals["items"] or 0,
            "cost": format_money(totals["cost"] or 0, settings),
            "mrp": format_money(totals["mrp"] or 0, settings),
        }},
    )


@shop_permission_required("stock.list")
def stock_card(request, pk):
    medicine = get_object_or_404(Medicine.objects.select_related("generic", "manufacturer", "sale_unit", "purchase_unit"), pk=pk)
    outlet_ids = [o.pk for o in request.allowed_outlets]
    stocks = list(BatchStock.objects.filter(medicine=medicine, outlet_id__in=outlet_ids, quantity__gt=0)
                  .select_related("batch", "outlet").order_by("outlet__code", "batch__expiry_date"))
    for stock in stocks:
        stock.quantity_text = format_quantity(stock.quantity, medicine)
    total = sum(s.quantity for s in stocks)
    return render(request, "stock/stock_card.html", {
        "medicine": medicine, "stocks": stocks, "total_text": format_quantity(total, medicine),
        "outlets": request.allowed_outlets, "today": timezone.localdate(),
        "soon": timezone.localdate() + timedelta(days=SOON_DAYS),
    })


REF_LINKS = {"purchase": "purchases:detail", "purchase_return": None, "sale": "sales:detail", "write_off": "inventory:damage",
             "transfer": "inventory:transfer", "stock_count": "inventory:count"}


@shop_permission_required("stock.list")
def movement_data(request, pk):
    medicine = get_object_or_404(Medicine.objects.select_related("sale_unit", "purchase_unit"), pk=pk)
    queryset = StockMovement.objects.filter(medicine=medicine, outlet_id__in=_outlet_ids(request)).select_related("batch", "outlet", "created_by")
    if request.GET.get("kind") in StockMovement.Kind.values:
        queryset = queryset.filter(kind=request.GET["kind"])

    def row(m):
        url_name = REF_LINKS.get(m.ref_type)
        ref = format_html('<a href="{}"><code>{}</code></a>', reverse(url_name, args=[m.ref_id]), m.ref_label) if url_name and m.ref_id else (m.ref_label or "—")
        return [
            timezone.localtime(m.created_at).strftime("%d %b %Y, %I:%M %p"),
            m.outlet.name,
            m.get_kind_display(),
            ref,
            str(m.batch),
            format_html('<span class="text-success fw-semibold">+{}</span>', m.quantity) if m.quantity > 0 else "",
            format_html('<span class="text-danger fw-semibold">{}</span>', m.quantity) if m.quantity < 0 else "",
            m.balance_after,
            str(m.created_by or "—"),
        ]

    return datatable_json(request, queryset.order_by("-created_at", "-id"),
                          order_columns=["created_at", "outlet__name", "kind", None, None, None, None, None, None], row=row)


# ---- Low stock ------------------------------------------------------------------

def _last_purchase(field):
    """A value from the medicine's latest received purchase line, e.g. its supplier's name."""
    from apps.purchases.models import PurchaseLine

    return Subquery(PurchaseLine.objects.filter(medicine=OuterRef("pk"), purchase__status="received")
                    .order_by("-purchase__date", "-id").values(field)[:1])


def low_stock_rows(request):
    """Medicines with a low-stock alert whose stock (in the chosen outlets) is at or below it."""
    queryset = stock_annotations(Medicine.objects.filter(is_active=True, alert_quantity__gt=0)
                                 .select_related("category", "sale_unit", "purchase_unit"), _outlet_ids(request))
    queryset = queryset.filter(on_hand__lte=F("alert_quantity")).annotate(last_supplier=_last_purchase("purchase__supplier__name"))
    if request.GET.get("show") == "out":
        queryset = queryset.filter(on_hand=0)
    return queryset


def reorder_units(medicine):
    """Suggested order: enough to get back to twice the alert level, rounded up to whole packs."""
    need = max(medicine.alert_quantity * 2 - medicine.on_hand, 0)
    pack = medicine.conversion or 1
    return -(-need // pack) * pack


@shop_permission_required("stock.list")
def low_stock(request):
    return render(request, "stock/low_stock.html", {"outlets": request.allowed_outlets, "current": getattr(request.outlet, "pk", None)})


@shop_permission_required("stock.list")
def low_stock_data(request):
    def row(m):
        out = m.on_hand == 0
        return [
            format_html("<code>{}</code>", m.code),
            format_html('<a class="fw-semibold" href="{}">{}</a>', reverse("stock:card", args=[m.pk]), m.display_name),
            m.category.name if m.category else "—",
            format_html('<span class="fw-semibold {}">{}</span>', "text-danger" if out else "text-warning-emphasis", format_quantity(m.on_hand, m)),
            format_quantity(m.alert_quantity, m),
            format_html('<span class="state-badge state-{}">{}</span>', "suspended" if out else "grace", _("Out of stock") if out else _("Low")),
            format_quantity(reorder_units(m), m),
            m.last_supplier or "—",
        ]

    counts = low_stock_rows(request).aggregate(total=Count("id"), out=Count("id", filter=Q(on_hand=0)))
    return datatable_json(request, low_stock_rows(request).order_by("on_hand", "name"),
                          order_columns=["code", "name", "category__name", "on_hand", "alert_quantity", None, None, "last_supplier"],
                          row=row, search=search_medicines, extra={"summary": {"low": counts["total"] - counts["out"], "out": counts["out"]}})


@shop_permission_required("stock.list")
def low_stock_export(request):
    rows = [(m.code, m.display_name, m.category.name if m.category else "", m.on_hand, m.alert_quantity, reorder_units(m),
             m.sale_unit.name, m.last_supplier or "") for m in low_stock_rows(request).order_by("last_supplier", "name")]
    headers = [_("Code"), _("Medicine"), _("Category"), _("On hand"), _("Alert at"), _("Suggested order"), _("Unit"), _("Last supplier")]
    return xlsx_response("low-stock.xlsx", headers, rows)


# ---- Expiring soon ------------------------------------------------------------

EXPIRY_WINDOWS = (0, 30, 60, 90, 180)  # 0 = already expired


def _window(request):
    days = request.GET.get("days", "")
    return int(days) if days.isdigit() and int(days) in EXPIRY_WINDOWS else 90


def expiring_rows(request, days):
    """Batches with stock that are expired or expire within `days` days (0 = expired only)."""
    from apps.purchases.models import PurchaseLine

    today = timezone.localdate()
    source = (PurchaseLine.objects.filter(batch=OuterRef("batch"), purchase__outlet=OuterRef("outlet"), purchase__status="received")
              .order_by("-purchase__date", "-id"))
    return (BatchStock.objects.filter(outlet_id__in=_outlet_ids(request), quantity__gt=0, batch__expiry_date__isnull=False,
                                      batch__expiry_date__lte=today + timedelta(days=days))
            .select_related("batch", "outlet", "medicine", "medicine__sale_unit", "medicine__purchase_unit")
            .annotate(purchase_id=Subquery(source.values("purchase_id")[:1]), supplier=Subquery(source.values("purchase__supplier__name")[:1])))


def _expiry_badge(expiry, today):
    left = (expiry - today).days
    if left < 0:
        return "expired", _("expired %(n)s days ago") % {"n": -left}
    if left == 0:
        return "expired", _("expires today")
    return ("grace" if left <= 30 else "trial"), _("in %(n)s days") % {"n": left}


@shop_permission_required("stock.list")
def expiring(request):
    chosen = request.GET.get("outlet", "")
    return render(request, "stock/expiring.html", {
        "outlets": request.allowed_outlets, "days": _window(request), "windows": EXPIRY_WINDOWS,
        "current": int(chosen) if chosen.isdigit() else getattr(request.outlet, "pk", None),
    })


@shop_permission_required("stock.list")
def expiring_data(request):
    settings = get_business_settings(request.tenant)
    today = timezone.localdate()
    can_write_off, can_return = has_perm(request.user, "damage.create"), has_perm(request.user, "purchase_return.create")

    def row(s):
        tone, text = _expiry_badge(s.batch.expiry_date, today)
        actions = []
        if can_write_off:
            reason = "expired" if s.batch.expiry_date <= today else "damaged"
            actions.append(format_html('<a class="btn btn-sm btn-soft tone-red text-nowrap" href="{}?outlet={}&batch={}&reason={}"><i class="bi bi-trash3"></i> {}</a>',
                                       reverse("inventory:damage_create"), s.outlet_id, s.batch_id, reason, _("Write off")))
        if can_return and s.purchase_id:
            actions.append(format_html('<a class="btn btn-sm btn-soft tone-sky text-nowrap" href="{}?batch={}"><i class="bi bi-arrow-return-left"></i> {}</a>',
                                       reverse("purchases:return_create", args=[s.purchase_id]), s.batch_id, _("Return to supplier")))
        return [
            format_html('<a class="fw-semibold" href="{}">{}</a>', reverse("stock:card", args=[s.medicine_id]), s.medicine.display_name),
            s.batch.batch_no or "—",
            format_html('{}<span class="d-block"><span class="state-badge state-{}">{}</span></span>', format_shop_date(s.batch.expiry_date, settings), tone, text),
            s.outlet.name,
            format_html('<span class="fw-semibold">{}</span>', format_quantity(s.quantity, s.medicine)),
            format_money(s.quantity * s.batch.unit_cost, settings),
            s.supplier or "—",
            format_html('<div class="d-flex gap-1 justify-content-end flex-wrap">{}</div>', format_html("".join(["{}"] * len(actions)), *actions)) if actions else "",
        ]

    # The tiles cover every window for the chosen outlets, whatever the table is filtered to.
    def value(*conditions):
        return Sum(F("quantity") * F("batch__unit_cost"), filter=Q(*conditions), output_field=MONEY)

    day = timedelta(days=1)
    tiles = expiring_rows(request, 90).aggregate(
        expired=value(Q(batch__expiry_date__lte=today)),
        d30=value(Q(batch__expiry_date__gt=today), Q(batch__expiry_date__lte=today + 30 * day)),
        d60=value(Q(batch__expiry_date__gt=today + 30 * day), Q(batch__expiry_date__lte=today + 60 * day)),
        d90=value(Q(batch__expiry_date__gt=today + 60 * day)),
        expired_count=Count("id", filter=Q(batch__expiry_date__lte=today)),
    )
    return datatable_json(
        request, expiring_rows(request, _window(request)).order_by("batch__expiry_date", "medicine__name"),
        order_columns=["medicine__name", "batch__batch_no", "batch__expiry_date", "outlet__name", "quantity", None, "supplier", None],
        row=row, search=lambda qs, t: qs.filter(Q(medicine__name__icontains=t) | Q(batch__batch_no__icontains=t)),
        extra={"summary": {
            "expired": format_money(tiles["expired"] or 0, settings), "d30": format_money(tiles["d30"] or 0, settings),
            "d60": format_money(tiles["d60"] or 0, settings), "d90": format_money(tiles["d90"] or 0, settings),
            "expiredCount": tiles["expired_count"],
        }},
    )


@shop_permission_required("stock.list")
def expiring_export(request):
    today = timezone.localdate()
    rows = [(s.medicine.display_name, s.batch.batch_no, s.batch.expiry_date.isoformat(), (s.batch.expiry_date - today).days, s.outlet.name,
             s.quantity, s.medicine.sale_unit.name, float(s.quantity * s.batch.unit_cost), s.supplier or "")
            for s in expiring_rows(request, _window(request)).order_by("batch__expiry_date", "medicine__name")]
    headers = [_("Medicine"), _("Batch"), _("Expiry"), _("Days left"), _("Outlet"), _("Quantity"), _("Unit"), _("Value (cost)"), _("Supplier")]
    return xlsx_response("expiring-stock.xlsx", headers, rows)


# ---- Opening stock ------------------------------------------------------------

OPENING_KEY = "opening_stock"


class OpeningForm(BootstrapFormMixin, forms.Form):
    outlet = forms.ModelChoiceField(label=gettext_lazy("Outlet"), queryset=Outlet.objects.none(), empty_label=None)
    file = forms.FileField(label=gettext_lazy("Excel or CSV file"), widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}))

    def __init__(self, *args, allowed_outlets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["outlet"].queryset = Outlet.objects.filter(pk__in=[o.pk for o in allowed_outlets])
        self.fields["outlet"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})


@shop_permission_required("stock.create")
def opening_template(request):
    return xlsx_response("opening-stock.xlsx", opening.HEADERS, opening.template_rows(), notes=opening.NOTES)


@shop_permission_required("stock.create")
def opening_upload(request):
    form = OpeningForm(request.POST or None, request.FILES or None, initial={"outlet": getattr(request.outlet, "pk", None)},
                       allowed_outlets=request.allowed_outlets)
    if request.method == "POST" and form.is_valid():
        try:
            rows, errors, total = opening.parse(form.cleaned_data["file"])
        except SpreadsheetError as error:
            form.add_error("file", str(error))
        else:
            outlet = form.cleaned_data["outlet"]
            staging.stage(request, OPENING_KEY, {"outlet": outlet.pk, "outlet_name": outlet.name, "file": form.cleaned_data["file"].name,
                                                  "total": total, "rows": rows, "errors": errors})
            return redirect("stock:opening_preview")
    return render(request, "stock/opening_upload.html", {"form": form})


@shop_permission_required("stock.create")
def opening_preview(request):
    staged = staging.load(request, OPENING_KEY)
    if staged is None:
        return redirect("stock:opening")
    outlet = next((o for o in request.allowed_outlets if o.pk == staged["outlet"]), None)
    if outlet is None:
        staging.clear(request, OPENING_KEY)
        return redirect("stock:opening")
    if request.method == "POST":
        if "confirm" in request.POST and staged["rows"]:
            count = opening.run(outlet, staged["rows"], user=request.user)
            messages.success(request, _("Opening stock added at %(outlet)s: %(count)s batches.") % {"outlet": outlet.name, "count": count})
            staging.clear(request, OPENING_KEY)
            return redirect("stock:list")
        staging.clear(request, OPENING_KEY)
        return redirect("stock:opening")
    units = sum(r["quantity"] for r in staged["rows"])
    return render(request, "stock/opening_preview.html", {
        "staged": staged, "sample": staged["rows"][:20], "errors": staged["errors"][:200], "units": units,
    })
