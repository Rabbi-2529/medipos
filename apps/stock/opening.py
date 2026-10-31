"""Opening stock: load what's already on the shelves, from Excel, into one outlet."""
from decimal import Decimal

from django.db import transaction

from apps.core.dates import parse_expiry
from apps.core.spreadsheets import map_headers, read_table
from apps.items.models import Medicine
from apps.items.services import to_decimal

from .models import StockMovement
from .services import get_or_create_batch, post_movement

MAX_ROWS = 30000
COLUMNS = [
    ("code", "Code", ["item code"]),
    ("barcode", "Barcode", []),
    ("name", "Name", ["medicine", "brand", "item name"]),
    ("batch_no", "Batch", ["batch no", "batch number", "lot"]),
    ("expiry", "Expiry", ["expiry date", "exp", "exp date"]),
    ("packs", "Packs", ["boxes", "purchase units"]),
    ("quantity", "Quantity", ["qty", "pieces", "units", "loose"]),
    ("unit_cost", "Unit cost", ["cost", "purchase price"]),
]
HEADERS = [label for _k, label, _a in COLUMNS]
ALIASES = {key: [key, label, *aliases] for key, label, aliases in COLUMNS}
NOTES = [
    "One row per batch on your shelves. Leave Quantity and Packs blank (or 0) for medicines you don't have.",
    "Quantity is in sale units (e.g. pieces). Packs is in purchase units (e.g. boxes) and is added to Quantity.",
    "Expiry: 2027-02, 02/2027 or 28/02/2027. Required for medicines that track expiry.",
    "Unit cost (per sale unit) values your stock; leave blank to use each medicine's purchase price.",
    "Importing again adds to the stock already there — it doesn't replace it.",
]


def template_rows():
    medicines = Medicine.objects.filter(is_active=True).order_by("name", "strength")
    for m in medicines.iterator(chunk_size=2000):
        yield [m.code, m.barcode, m.display_name, "", "", "", "", float(m.purchase_price)]


def parse(upload):
    """(rows, errors, total). Rows hold medicine ids and parsed values; nothing is saved."""
    headers, data = read_table(upload)
    mapping = map_headers(headers, ALIASES)
    if not {"code", "barcode", "name"} & set(mapping):
        return [], [(1, "No Code, Barcode or Name column found.")], len(data)
    if len(data) > MAX_ROWS:
        return [], [(0, f"Too many rows ({len(data)}); split the file.")], len(data)

    medicines = list(Medicine.objects.filter(is_active=True).select_related("sale_unit", "purchase_unit"))
    by_code = {m.code: m for m in medicines}
    by_barcode = {m.barcode: m for m in medicines if m.barcode}
    by_name = {}
    for m in medicines:
        for key in {m.display_name.lower(), m.name.lower()}:
            by_name.setdefault(key, []).append(m)

    def cell(values, key):
        index = mapping.get(key)
        if index is None or index >= len(values):
            return ""
        value = values[index]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value if key == "expiry" else str(value).strip()

    rows, errors = [], []
    for number, values in enumerate(data, start=2):
        try:
            packs = int(to_decimal(cell(values, "packs"), Decimal("0")))
            loose = int(to_decimal(cell(values, "quantity"), Decimal("0")))
        except (ValueError, ArithmeticError):
            errors.append((number, "quantity must be a whole number"))
            continue
        if packs == 0 and loose == 0:
            continue  # nothing on the shelf for this row
        if packs < 0 or loose < 0:
            errors.append((number, "quantity can't be negative"))
            continue
        code, barcode, name = cell(values, "code"), cell(values, "barcode"), cell(values, "name")
        medicine = by_code.get(code) or by_barcode.get(barcode)
        if medicine is None and name:
            matches = by_name.get(name.lower(), [])
            if len(matches) > 1:
                errors.append((number, f"“{name}” matches several medicines; add the Code"))
                continue
            medicine = matches[0] if matches else None
        if medicine is None:
            errors.append((number, f"no medicine with code “{code}”, barcode “{barcode}” or name “{name}”"))
            continue
        try:
            expiry = parse_expiry(cell(values, "expiry"))
            cost = to_decimal(cell(values, "unit_cost"), medicine.purchase_price)
        except ValueError as error:
            errors.append((number, str(error)))
            continue
        if medicine.track_expiry and expiry is None:
            errors.append((number, f"{medicine.display_name}: expiry is required"))
            continue
        if cost < 0:
            errors.append((number, "unit cost can't be negative"))
            continue
        rows.append({
            "row_number": number, "medicine": medicine.pk, "name": medicine.display_name,
            "batch_no": cell(values, "batch_no")[:40], "expiry": expiry.isoformat() if expiry else "",
            "quantity": packs * medicine.conversion + loose, "unit_cost": str(cost),
        })
    return rows, errors, len(data)


@transaction.atomic
def run(outlet, rows, *, user=None):
    medicines = {m.pk: m for m in Medicine.objects.filter(pk__in={r["medicine"] for r in rows})}
    ref = ("opening", None, "Opening stock")
    for row in rows:
        medicine = medicines[row["medicine"]]
        expiry = parse_expiry(row["expiry"]) if row["expiry"] else None
        cost = Decimal(row["unit_cost"])
        batch = get_or_create_batch(medicine, row["batch_no"], expiry, cost)
        post_movement(outlet=outlet, batch=batch, quantity=row["quantity"], kind=StockMovement.Kind.OPENING,
                      unit_cost=cost, ref=ref, user=user)
    from apps.accounting import posting

    posting.post_opening_stock(outlet)
    return len(rows)
