"""Excel/CSV import of medicines: parse + validate (preview), then create/update in bulk."""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from apps.core.spreadsheets import map_headers, read_table

from .choices import parse_dosage_form
from .models import Category, Generic, Manufacturer, Medicine, Rack, Unit, build_search_text
from .services import BATCH, bulk_create_medicines, clean_name, resolve_names, to_decimal

MAX_ROWS = 30000

# (field, header in our template/export, other accepted spellings)
COLUMNS = [
    ("code", "Code", ["item code", "sku"]),
    ("name", "Name", ["brand", "brand name", "item name", "medicine", "medicine name", "product"]),
    ("strength", "Strength", ["power"]),
    ("dosage_form", "Dosage form", ["form", "dosage"]),
    ("generic", "Generic", ["generic name", "molecule"]),
    ("manufacturer", "Manufacturer", ["company", "brand owner", "pharma"]),
    ("category", "Category", []),
    ("kind", "Type", ["item type"]),
    ("sale_unit", "Sale unit", ["unit"]),
    ("purchase_unit", "Purchase unit", ["pack unit"]),
    ("conversion", "Units per purchase unit", ["conversion", "conversion rate", "pack quantity"]),
    ("strip_size", "Pieces per strip", ["strip size"]),
    ("purchase_price", "Purchase price", ["cost", "cost price", "tp", "trade price"]),
    ("sale_price", "Sale price", ["mrp", "price", "retail price", "unit price"]),
    ("wholesale_price", "Wholesale price", ["wholesale"]),
    ("barcode", "Barcode", ["ean", "upc"]),
    ("rack", "Rack", ["shelf"]),
    ("alert_quantity", "Alert quantity", ["alert stock", "reorder level", "low stock alert"]),
    ("prescription_required", "Prescription required", ["rx", "prescription"]),
    ("track_expiry", "Track expiry", ["expiry", "expiry date maintain"]),
    ("alternative_name", "Alternative name", ["alias", "bangla name"]),
]
HEADERS = [label for _key, label, _aliases in COLUMNS]
ALIASES = {key: [key, label, *aliases] for key, label, aliases in COLUMNS}

YES = {"yes", "y", "true", "1", "rx"}
NO = {"no", "n", "false", "0", "otc"}


@dataclass
class ImportPreview:
    total: int = 0
    to_create: list = field(default_factory=list)
    to_update: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # (row number, message)
    missing_name_column: bool = False
    columns: list = field(default_factory=list)


def _cell(values, mapping, key):
    index = mapping.get(key)
    if index is None or index >= len(values):
        return ""
    value = values[index]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _int(text, minimum):
    if text == "":
        return None
    number = to_decimal(text)
    if number != number.to_integral_value() or number < minimum:
        raise ValueError(f"must be a whole number of at least {minimum}")
    return int(number)


def _money(text):
    value = to_decimal(text)
    if value is not None and value < 0:
        raise ValueError("can't be negative")
    return value


def _flag(text):
    lowered = text.lower()
    if lowered in YES:
        return True
    if lowered in NO:
        return False
    if lowered == "":
        return None
    raise ValueError("use yes or no")


def parse_upload(upload, *, update_existing=False):
    """Read and check every row without saving anything. Must run inside the tenant's context."""
    headers, rows = read_table(upload)
    mapping = map_headers(headers, ALIASES)
    preview = ImportPreview(total=len(rows), columns=sorted(mapping))
    if "name" not in mapping and not (update_existing and "code" in mapping):
        preview.missing_name_column = True
        return preview
    if len(rows) > MAX_ROWS:
        preview.errors.append((0, f"Too many rows ({len(rows)}). Split the file into parts of {MAX_ROWS} or fewer."))
        return preview

    existing = dict(Medicine.objects.values_list("code", "pk"))
    barcode_owner = {b: c for c, b in Medicine.objects.exclude(barcode="").values_list("code", "barcode")}
    seen_codes, seen_barcodes = set(), set()

    for number, values in enumerate(rows, start=2):  # row 1 is the header
        try:
            row = {"row_number": number}
            for key in ("code", "name", "strength", "generic", "manufacturer", "category", "sale_unit",
                        "purchase_unit", "barcode", "rack", "alternative_name"):
                row[key] = clean_name(_cell(values, mapping, key))
            row["dosage_form"] = parse_dosage_form(_cell(values, mapping, "dosage_form"))
            kind = _cell(values, mapping, "kind").lower()
            row["kind"] = Medicine.Kind.GENERAL if kind.startswith("gen") else Medicine.Kind.MEDICINE
            row["conversion"] = _int(_cell(values, mapping, "conversion"), 1)
            row["strip_size"] = _int(_cell(values, mapping, "strip_size"), 1)
            row["alert_quantity"] = _int(_cell(values, mapping, "alert_quantity"), 0)
            for key in ("purchase_price", "sale_price", "wholesale_price"):
                row[key] = _money(_cell(values, mapping, key))
            row["prescription_required"] = _flag(_cell(values, mapping, "prescription_required"))
            row["track_expiry"] = _flag(_cell(values, mapping, "track_expiry"))
        except ValueError as error:
            preview.errors.append((number, str(error)))
            continue

        code, barcode = row["code"], row["barcode"]
        updating = update_existing and code in existing
        if not updating and not row["name"]:
            preview.errors.append((number, "name is missing"))
            continue
        if code and code in seen_codes:
            preview.errors.append((number, f"code {code} appears twice in the file"))
            continue
        if code and code in existing and not update_existing:
            preview.errors.append((number, f"code {code} is already used by another medicine"))
            continue
        if barcode:
            if barcode in seen_barcodes:
                preview.errors.append((number, f"barcode {barcode} appears twice in the file"))
                continue
            owner = barcode_owner.get(barcode)
            if owner and owner != code:
                preview.errors.append((number, f"barcode {barcode} already belongs to medicine {owner}"))
                continue
        if code:
            seen_codes.add(code)
        if barcode:
            seen_barcodes.add(barcode)
        (preview.to_update if updating else preview.to_create).append(row)
    return preview


def rows_to_json(rows):
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()} for row in rows]


def rows_from_json(rows):
    for row in rows:
        for key in ("purchase_price", "sale_price", "wholesale_price"):
            if row.get(key) is not None:
                row[key] = Decimal(row[key])
    return rows


@transaction.atomic
def run_import(tenant, to_create, to_update):
    """Apply a checked preview. Returns (created, updated)."""
    created = bulk_create_medicines(tenant, [{**r, "track_expiry": r.get("track_expiry") is not False} for r in to_create])
    updated = _apply_updates(to_update) if to_update else 0
    return created, updated


def _apply_updates(rows):
    names = {"generic": Generic, "manufacturer": Manufacturer, "category": Category, "rack": Rack}
    resolved = {key: resolve_names(model, [r.get(key) for r in rows]) for key, model in names.items()}
    units = resolve_names(Unit, [r.get("sale_unit") for r in rows] + [r.get("purchase_unit") for r in rows])
    by_code = {r["code"]: r for r in rows}
    medicines = list(Medicine.objects.filter(code__in=by_code).select_related("generic", "manufacturer"))
    plain = ("name", "strength", "dosage_form", "barcode", "alternative_name", "conversion", "strip_size",
             "purchase_price", "sale_price", "wholesale_price", "alert_quantity", "prescription_required", "track_expiry")
    for medicine in medicines:
        row = by_code[medicine.code]
        for key in plain:  # empty cells leave the current value alone
            if row.get(key) not in (None, ""):
                setattr(medicine, key, row[key])
        for key in names:
            if row.get(key):
                setattr(medicine, key, resolved[key][row[key].lower()])
        for key in ("sale_unit", "purchase_unit"):
            if row.get(key):
                setattr(medicine, key, units[row[key].lower()])
        if medicine.purchase_unit_id == medicine.sale_unit_id:
            medicine.conversion = 1
        medicine.search_text = build_search_text(
            name=medicine.name, strength=medicine.strength, dosage_form=medicine.dosage_form,
            generic=medicine.generic.name if medicine.generic else "",
            manufacturer=medicine.manufacturer.name if medicine.manufacturer else "",
            alternative_name=medicine.alternative_name, code=medicine.code, barcode=medicine.barcode,
        )
    Medicine.objects.bulk_update(medicines, [*plain, "generic", "manufacturer", "category", "rack", "sale_unit",
                                             "purchase_unit", "search_text"], batch_size=BATCH)
    return len(medicines)


def export_rows(queryset):
    """Rows in template column order, so an export can be edited and imported back."""
    for m in queryset.select_related("generic", "manufacturer", "category", "rack", "sale_unit", "purchase_unit").iterator(chunk_size=2000):
        yield [
            m.code, m.name, m.strength, m.get_dosage_form_display() if m.dosage_form else "",
            m.generic.name if m.generic else "", m.manufacturer.name if m.manufacturer else "",
            m.category.name if m.category else "", m.get_kind_display(), m.sale_unit.name, m.purchase_unit.name,
            m.conversion, m.strip_size or "", float(m.purchase_price), float(m.sale_price),
            float(m.wholesale_price) if m.wholesale_price is not None else "", m.barcode, m.rack.name if m.rack else "",
            m.alert_quantity, "yes" if m.prescription_required else "no", "yes" if m.track_expiry else "no",
            m.alternative_name,
        ]


TEMPLATE_EXAMPLES = [
    ["", "Napa", "500 mg", "Tablet", "Paracetamol", "Beximco Pharmaceuticals", "Medicine", "Medicine", "Piece", "Box",
     200, 10, 0.95, 1.20, "", "", "A-1", 100, "no", "yes", ""],
    ["", "Johnson's Baby Lotion", "100 ml", "Lotion", "", "Johnson & Johnson", "Baby care", "General", "Bottle", "Bottle",
     1, "", 210, 250, "", "8901012345678", "C-3", 5, "no", "no", ""],
]
TEMPLATE_NOTES = [
    "One row per medicine or shop item. Only Name is required; leave the rest blank if you don't know them.",
    "Code: leave blank to get the next number automatically. To UPDATE existing medicines, keep their Code and tick 'update' when importing.",
    "Prices are per sale unit (e.g. per piece). 'Units per purchase unit' = how many sale units are in one purchase unit (e.g. 200 pieces per box).",
    "Dosage form: Tablet, Capsule, Syrup, Suspension, Drops, Injection, Cream, Ointment, … Short forms like Tab, Cap, Syr, Inj are understood.",
    "Generic, Manufacturer, Category, Rack and Units are created automatically if they don't exist yet.",
    "Type: Medicine or General. Prescription required / Track expiry: yes or no.",
]
