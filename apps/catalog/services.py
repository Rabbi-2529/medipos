"""Shared medicine catalog: bulk import (platform) and adoption into a pharmacy's medicine list."""
from django.db import transaction

from apps.core.spreadsheets import map_headers, read_table
from apps.items.choices import parse_dosage_form
from apps.items.models import Medicine
from apps.items.services import bulk_create_medicines, clean_name, to_decimal

from .models import CatalogMedicine, catalog_search_text

BATCH = 1000
COLUMNS = [
    ("brand_name", "Brand name", ["brand", "name", "trade name", "medicine name"]),
    ("generic_name", "Generic", ["generic name", "molecule", "composition"]),
    ("strength", "Strength", []),
    ("dosage_form", "Dosage form", ["form", "dosage"]),
    ("manufacturer", "Manufacturer", ["company", "manufacturer name"]),
    ("pack_size", "Pack size", ["pack", "packing"]),
    ("unit_price", "Unit price", ["price", "mrp", "unit price (tk)"]),
]
HEADERS = [label for _k, label, _a in COLUMNS]
ALIASES = {key: [key, label, *aliases] for key, label, aliases in COLUMNS}
EXAMPLE_ROWS = [["Napa", "Paracetamol", "500 mg", "Tablet", "Beximco Pharmaceuticals Ltd.", "10 x 10", ""]]


def _key(brand, strength, form, maker):
    return (brand, strength, form, maker)


@transaction.atomic
def import_catalog(upload):
    """Add new catalog rows and refresh existing ones (same brand + strength + form + maker). Returns (created, updated, errors)."""
    headers, rows = read_table(upload)
    mapping = map_headers(headers, ALIASES)
    if "brand_name" not in mapping:
        return 0, 0, [(1, "No “Brand name” column found.")]

    def cell(values, key):
        index = mapping.get(key)
        return "" if index is None or index >= len(values) else str(values[index]).strip()

    existing = {_key(c.brand_name, c.strength, c.dosage_form, c.manufacturer): c for c in CatalogMedicine.objects.all()}
    new, changed, errors, seen = [], {}, [], set()
    for number, values in enumerate(rows, start=2):
        brand = clean_name(cell(values, "brand_name"))
        if not brand:
            errors.append((number, "brand name is missing"))
            continue
        try:
            price = to_decimal(cell(values, "unit_price").replace("৳", ""))
        except ValueError as error:
            errors.append((number, str(error)))
            continue
        fields = {
            "generic_name": clean_name(cell(values, "generic_name"))[:255],
            "pack_size": clean_name(cell(values, "pack_size"))[:60],
            "unit_price": price,
        }
        strength = clean_name(cell(values, "strength"))[:60]
        form = parse_dosage_form(cell(values, "dosage_form"))
        maker = clean_name(cell(values, "manufacturer"))
        key = _key(brand, strength, form, maker)
        if key in seen:
            errors.append((number, "duplicate of an earlier row"))
            continue
        seen.add(key)
        text = catalog_search_text(brand, strength, form, fields["generic_name"], maker)
        if key in existing:
            row = existing[key]
            for name, value in fields.items():
                setattr(row, name, value)
            row.search_text = text
            row.is_active = True
            changed[row.pk] = row
        else:
            new.append(CatalogMedicine(brand_name=brand, strength=strength, dosage_form=form, manufacturer=maker,
                                       search_text=text, **fields))
    CatalogMedicine.objects.bulk_create(new, batch_size=BATCH)
    CatalogMedicine.objects.bulk_update(list(changed.values()), ["generic_name", "pack_size", "unit_price", "search_text", "is_active"], batch_size=BATCH)
    return len(new), len(changed), errors


def search_catalog(queryset, term):
    for word in term.lower().split():
        queryset = queryset.filter(search_text__contains=word)
    return queryset


def adopted_ids():
    """Catalog ids the current pharmacy already has as medicines."""
    return set(Medicine.objects.exclude(catalog_ref=None).values_list("catalog_ref_id", flat=True))


def adopt(tenant, catalog_rows, *, category="Medicine"):
    """Copy catalog entries into the pharmacy's medicines. Returns (added, skipped). Runs in the tenant's context."""
    already = adopted_ids()
    picked = [c for c in catalog_rows if c.pk not in already]
    rows = [{
        "name": c.brand_name, "strength": c.strength, "dosage_form": c.dosage_form, "generic": c.generic_name,
        "manufacturer": c.manufacturer, "category": category, "sale_unit": "Piece", "purchase_unit": "Piece",
        "sale_price": c.unit_price,
    } for c in picked]
    added = bulk_create_medicines(tenant, rows, catalog_refs={i: c.pk for i, c in enumerate(picked)})
    return added, len(catalog_rows) - len(picked)
