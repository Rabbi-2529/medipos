"""Creating medicines one at a time or by the thousand (imports, catalog)."""
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.billing.limits import check_limit
from apps.core.tenancy import get_current_tenant
from apps.setup.services import reserve_codes

from .models import Category, Generic, Manufacturer, Medicine, Rack, Unit, build_search_text

CODE_KEY, CODE_PADDING = "item", 6
BATCH = 1000


def clean_name(value):
    return " ".join(str(value or "").split())[:150]


def allocate_codes(tenant, count):
    """`count` fresh medicine codes, skipping any already typed in by hand."""
    taken = set(Medicine.all_objects.filter(tenant=tenant).values_list("code", flat=True))
    codes = []
    while len(codes) < count:
        batch = reserve_codes(tenant, CODE_KEY, count - len(codes), padding=CODE_PADDING)
        codes += [code for code in batch if code not in taken]
    return codes


def resolve_names(model, names):
    """{lowercase name: row} for every name, creating the missing ones in bulk (case-insensitive)."""
    wanted = {clean_name(n).lower(): clean_name(n) for n in names if clean_name(n)}
    found = {obj.name.lower(): obj for obj in model.objects.all()}
    missing = [model(name=wanted[key], tenant=get_current_tenant()) for key in wanted if key not in found]
    if missing:
        # bulk_create skips save(), so the tenant is stamped above.
        model.objects.bulk_create(missing, batch_size=BATCH, ignore_conflicts=True)
        found = {obj.name.lower(): obj for obj in model.objects.all()}
    return found


def to_decimal(value, default=None):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"not a number: {value}")


@transaction.atomic
def bulk_create_medicines(tenant, rows, *, catalog_refs=None):
    """Create medicines from normalized rows (names, not ids). Returns the created count.

    Each row: name, strength, dosage_form, generic, manufacturer, category, rack, sale_unit,
    purchase_unit, conversion, strip_size, purchase_price, sale_price, wholesale_price,
    barcode, code, alert_quantity, prescription_required, track_expiry, kind, alternative_name.
    Must run inside the tenant's context (request or tenant_context).
    """
    if not rows:
        return 0
    check_limit(tenant, "medicines", adding=len(rows))
    generics = resolve_names(Generic, [r.get("generic") for r in rows])
    makers = resolve_names(Manufacturer, [r.get("manufacturer") for r in rows])
    categories = resolve_names(Category, [r.get("category") for r in rows])
    racks = resolve_names(Rack, [r.get("rack") for r in rows])
    units = resolve_names(Unit, [r.get("sale_unit") or "Piece" for r in rows] + [r.get("purchase_unit") for r in rows])
    auto_codes = iter(allocate_codes(tenant, sum(1 for r in rows if not r.get("code"))))

    def lookup(mapping, name):
        name = clean_name(name).lower()
        return mapping.get(name) if name else None

    objects = []
    for index, row in enumerate(rows):
        sale_unit = lookup(units, row.get("sale_unit") or "Piece")
        purchase_unit = lookup(units, row.get("purchase_unit")) or sale_unit
        generic, maker = lookup(generics, row.get("generic")), lookup(makers, row.get("manufacturer"))
        medicine = Medicine(
            tenant=tenant,
            kind=row.get("kind") or Medicine.Kind.MEDICINE,
            name=clean_name(row["name"]),
            strength=clean_name(row.get("strength"))[:60],
            dosage_form=row.get("dosage_form") or "",
            generic=generic,
            manufacturer=maker,
            category=lookup(categories, row.get("category")),
            rack=lookup(racks, row.get("rack")),
            alternative_name=clean_name(row.get("alternative_name")),
            code=row.get("code") or next(auto_codes),
            barcode=(row.get("barcode") or "").strip(),
            sale_unit=sale_unit,
            purchase_unit=purchase_unit,
            conversion=1 if purchase_unit == sale_unit else (row.get("conversion") or 1),
            strip_size=row.get("strip_size") or None,
            purchase_price=row.get("purchase_price") or Decimal("0"),
            sale_price=row.get("sale_price") or Decimal("0"),
            wholesale_price=row.get("wholesale_price"),
            alert_quantity=row.get("alert_quantity") or 0,
            prescription_required=bool(row.get("prescription_required")),
            track_expiry=row.get("track_expiry") is not False,
            catalog_ref_id=(catalog_refs or {}).get(index),
        )
        medicine.search_text = build_search_text(
            name=medicine.name, strength=medicine.strength, dosage_form=medicine.dosage_form,
            generic=generic.name if generic else "", manufacturer=maker.name if maker else "",
            alternative_name=medicine.alternative_name, code=medicine.code, barcode=medicine.barcode,
        )
        objects.append(medicine)
    Medicine.objects.bulk_create(objects, batch_size=BATCH)
    return len(objects)


def search_medicines(queryset, term):
    """Every word must appear somewhere in the name/strength/generic/maker/code/barcode text."""
    for word in term.lower().split():
        queryset = queryset.filter(search_text__contains=word)
    return queryset
