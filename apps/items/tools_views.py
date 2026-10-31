"""Add from catalog, bulk price update, barcode labels."""
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.billing.limits import PlanLimitReached
from apps.catalog.models import CatalogMedicine
from apps.catalog.services import adopt, adopted_ids, search_catalog
from apps.core.datatables import datatable_json
from apps.people.access import shop_permission_required
from apps.setup.services import format_money, get_business_settings

from .models import Medicine
from .services import BATCH
from .views import filtered_medicines

ADOPT_MAX = 25000

# ---- Add from the shared catalog ---------------------------------------------------


def _catalog_queryset(params):
    queryset = CatalogMedicine.objects.filter(is_active=True)
    if params.get("manufacturer"):
        queryset = queryset.filter(manufacturer__icontains=params["manufacturer"])
    if params.get("form"):
        queryset = queryset.filter(dosage_form=params["form"])
    term = params.get("search[value]", "") or params.get("q", "")
    if term.strip():
        queryset = search_catalog(queryset, term)
    return queryset


@shop_permission_required("item.create")
def catalog_browse(request):
    from .choices import DosageForm

    return render(request, "items/catalog_browse.html", {
        "catalog_total": CatalogMedicine.objects.filter(is_active=True).count(),
        "adopted": len(adopted_ids()),
        "forms": DosageForm.choices,
    })


@shop_permission_required("item.create")
def catalog_data(request):
    have = adopted_ids()

    def row(c):
        added = c.pk in have
        box = (format_html('<span class="badge text-bg-success">{}</span>', _("Added")) if added else
               format_html('<input class="form-check-input" type="checkbox" name="ids" value="{}" form="adopt-form" aria-label="{}">', c.pk, c.display_name))
        return [box, format_html('<span class="fw-semibold">{}</span>', c.display_name), c.generic_name or "—",
                c.manufacturer or "—", c.pack_size or "—", f"{c.unit_price}" if c.unit_price is not None else "—"]

    params = request.GET.copy()
    params.pop("search[value]", None)
    return datatable_json(
        request, _catalog_queryset(params).order_by("brand_name", "strength"),
        order_columns=[None, "brand_name", "generic_name", "manufacturer", None, "unit_price"],
        row=row, search=search_catalog,
    )


@shop_permission_required("item.create")
@require_POST
def catalog_adopt(request):
    if request.POST.get("scope") == "all":
        rows = list(_catalog_queryset(request.POST)[:ADOPT_MAX + 1])
        if len(rows) > ADOPT_MAX:
            messages.error(request, _("That's more than %(max)s medicines. Narrow it down with a filter.") % {"max": ADOPT_MAX})
            return redirect("items:catalog")
    else:
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        rows = list(CatalogMedicine.objects.filter(pk__in=ids, is_active=True))
    if not rows:
        messages.info(request, _("Tick the medicines you want first."))
        return redirect("items:catalog")
    try:
        added, skipped = adopt(request.tenant, rows)
    except PlanLimitReached as error:
        messages.error(request, str(error))
        return redirect("items:catalog")
    text = _("%(added)s medicines added to your list.") % {"added": added}
    if skipped:
        text += " " + _("%(skipped)s were already there.") % {"skipped": skipped}
    messages.success(request, text + " " + _("Set their prices and units before selling."))
    return redirect("items:catalog")


# ---- Bulk price update -------------------------------------------------------------

PRICE_FIELDS = {"sale_price": "Sale price (MRP)", "purchase_price": "Purchase price", "wholesale_price": "Wholesale price"}
METHODS = {"increase": "Increase by %", "decrease": "Decrease by %", "margin": "Set margin % over purchase price"}
ROUNDING = {"cents": "Keep paisa (0.01)", "half": "Nearest 0.50", "whole": "Nearest whole taka"}


def _round(value, rounding, field, direction):
    """Round toward `direction` so an increase never ends up lower than before (and a decrease never higher)."""
    mode = ROUND_CEILING if direction == "up" else ROUND_FLOOR
    if rounding == "whole":
        return value.quantize(Decimal("1"), mode)
    if rounding == "half":
        return (value * 2).quantize(Decimal("1"), mode) / 2
    return value.quantize(Decimal("0.0001") if field == "purchase_price" else Decimal("0.01"), mode)


def new_price(medicine, field, method, percent, rounding):
    """The price after the change, or None when it can't be worked out (e.g. no purchase price for a margin)."""
    factor = Decimal(percent) / 100
    if method == "margin":
        if not medicine.purchase_price:
            return None
        value = medicine.purchase_price * (1 + factor)
    else:
        current = getattr(medicine, field)
        if current is None:
            return None
        value = current * (1 + factor if method == "increase" else 1 - factor)
    return max(Decimal("0"), _round(value, rounding, field, "down" if method == "decrease" else "up"))


def _price_request(params):
    field = params.get("field") if params.get("field") in PRICE_FIELDS else None
    method = params.get("method") if params.get("method") in METHODS else None
    rounding = params.get("rounding") if params.get("rounding") in ROUNDING else "cents"
    try:
        percent = Decimal(params.get("percent", ""))
    except Exception:
        percent = None
    if method == "margin" and field == "purchase_price":
        method = None
    if not (field and method) or percent is None or percent < 0 or (method == "decrease" and percent > 100):
        return None
    return field, method, percent, rounding


@shop_permission_required("item.edit")
def bulk_price(request):
    params = request.POST if request.method == "POST" else request.GET
    change = _price_request(params)
    queryset = filtered_medicines(params) if change else Medicine.objects.none()
    settings = get_business_settings(request.tenant)

    if request.method == "POST":
        if not change:
            messages.error(request, _("Choose what to change and a valid percentage."))
            return redirect("items:bulk_price")
        field, method, percent, rounding = change
        updated = []
        with transaction.atomic():
            for medicine in queryset.iterator(chunk_size=BATCH):
                value = new_price(medicine, field, method, percent, rounding)
                if value is not None and value != getattr(medicine, field):
                    setattr(medicine, field, value)
                    updated.append(medicine)
            Medicine.objects.bulk_update(updated, [field], batch_size=BATCH)
        messages.success(request, _("%(count)s prices updated.") % {"count": len(updated)})
        return redirect("items:list")

    preview = []
    if change:
        field, method, percent, rounding = change
        for medicine in queryset.select_related("generic")[:15]:
            value = new_price(medicine, field, method, percent, rounding)
            preview.append({
                "medicine": medicine,
                "old": format_money(getattr(medicine, field) or 0, settings),
                "new": format_money(value, settings) if value is not None else None,
            })
    return render(request, "items/bulk_price.html", {
        "fields": PRICE_FIELDS, "methods": METHODS, "roundings": ROUNDING,
        "change": change, "count": queryset.count() if change else 0, "preview": preview, "params": params,
    })


# ---- Barcode labels ----------------------------------------------------------------


@shop_permission_required("item.list")
def labels(request):
    settings = get_business_settings(request.tenant)
    return render(request, "items/labels.html", {
        "currency": settings.currency_symbol,
        "decimals": settings.decimal_places,
        "shop_name": request.tenant.name,
    })
