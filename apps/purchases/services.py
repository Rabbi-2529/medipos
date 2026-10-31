"""Purchases: draft -> received (stock in + supplier ledger), payments, returns."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounting import posting
from apps.core.dates import parse_expiry
from apps.items.models import Medicine
from apps.setup.services import next_code
from apps.stock.models import StockMovement
from apps.stock.services import StockError, get_or_create_batch, post_movement
from apps.suppliers.models import SupplierLedgerEntry
from apps.suppliers.services import post_entry

from .models import Purchase, PurchaseLine, PurchaseReturn, PurchaseReturnLine

CENT = Decimal("0.01")
COST = Decimal("0.0001")


class PurchaseError(Exception):
    """A user-facing reason a purchase change was refused."""


def _int(value, name, row):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise PurchaseError(_("Line %(row)s: %(name)s must be a whole number.") % {"row": row, "name": name})
    if number < 0:
        raise PurchaseError(_("Line %(row)s: %(name)s can't be negative.") % {"row": row, "name": name})
    return number


def _money(value, name, row):
    try:
        number = Decimal(str(value or 0))
    except InvalidOperation:
        raise PurchaseError(_("Line %(row)s: %(name)s must be a number.") % {"row": row, "name": name})
    if number < 0:
        raise PurchaseError(_("Line %(row)s: %(name)s can't be negative.") % {"row": row, "name": name})
    return number


def clean_lines(raw_lines, *, today=None):
    """Validate lines posted by the purchase form. Returns dicts ready for PurchaseLine (totals computed here)."""
    today = today or timezone.localdate()
    if not raw_lines:
        raise PurchaseError(_("Add at least one medicine."))
    ids = {str(raw.get("medicine")) for raw in raw_lines}
    medicines = {str(m.pk): m for m in Medicine.objects.filter(pk__in=[i for i in ids if i.isdigit()])}
    lines = []
    for row, raw in enumerate(raw_lines, start=1):
        medicine = medicines.get(str(raw.get("medicine")))
        if medicine is None:
            raise PurchaseError(_("Line %(row)s: pick a medicine.") % {"row": row})
        packs = _int(raw.get("packs"), _("quantity"), row)
        units = _int(raw.get("units"), _("loose units"), row)
        free = _int(raw.get("free_units"), _("free units"), row)
        conversion = medicine.conversion
        if packs * conversion + units + free == 0:
            raise PurchaseError(_("Line %(row)s (%(name)s): enter a quantity.") % {"row": row, "name": medicine.display_name})
        pack_cost = _money(raw.get("pack_cost"), _("cost"), row)
        sale_price = _money(raw.get("sale_price"), _("MRP"), row)
        try:
            expiry = parse_expiry(raw.get("expiry"))
        except ValueError as error:
            raise PurchaseError(_("Line %(row)s: %(error)s") % {"row": row, "error": error})
        if medicine.track_expiry and expiry is None:
            raise PurchaseError(_("Line %(row)s (%(name)s): expiry date is required.") % {"row": row, "name": medicine.display_name})
        if expiry is not None and expiry <= today:
            raise PurchaseError(_("Line %(row)s (%(name)s): this batch has already expired.") % {"row": row, "name": medicine.display_name})
        line_total = (pack_cost * packs + pack_cost * units / conversion).quantize(CENT, ROUND_HALF_UP)
        lines.append({
            "medicine": medicine, "batch_no": str(raw.get("batch_no") or "").strip()[:40], "expiry_date": expiry,
            "conversion": conversion, "packs": packs, "units": units, "free_units": free,
            "pack_cost": pack_cost.quantize(COST), "sale_price": sale_price.quantize(CENT), "line_total": line_total,
        })
    return lines


@transaction.atomic
def save_draft(*, tenant, outlet, supplier, date, lines, purchase=None, supplier_invoice_no="", discount=Decimal("0"),
               note="", update_prices=True, user=None):
    if purchase is not None and not purchase.is_draft:
        raise PurchaseError(_("A received purchase can't be changed. Return items instead."))
    subtotal = sum((line["line_total"] for line in lines), Decimal("0"))
    discount = Decimal(discount or 0).quantize(CENT)
    if discount < 0 or discount > subtotal:
        raise PurchaseError(_("Discount must be between 0 and the subtotal."))
    fields = {
        "outlet": outlet, "supplier": supplier, "date": date, "supplier_invoice_no": supplier_invoice_no,
        "subtotal": subtotal, "discount": discount, "total": subtotal - discount, "note": note, "update_prices": update_prices,
    }
    if purchase is None:
        purchase = Purchase.objects.create(number=next_code(tenant, "purchase", prefix="PUR-", padding=5), created_by=user, **fields)
    else:
        for name, value in fields.items():
            setattr(purchase, name, value)
        purchase.save()
        purchase.lines.all().delete()
    PurchaseLine.objects.bulk_create([PurchaseLine(tenant=tenant, purchase=purchase, **line) for line in lines])
    return purchase


@transaction.atomic
def receive(purchase, *, user=None):
    """Post every line into stock at the purchase's outlet and add the total to the supplier's ledger."""
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
    if not purchase.is_draft:
        raise PurchaseError(_("%(number)s was already received.") % {"number": purchase.number})
    ref = ("purchase", purchase.pk, purchase.number)
    for line in purchase.lines.select_related("medicine"):
        unit_cost = line.unit_cost.quantize(COST)
        batch = get_or_create_batch(line.medicine, line.batch_no, line.expiry_date, unit_cost)
        post_movement(outlet=purchase.outlet, batch=batch, quantity=line.total_units, kind=StockMovement.Kind.PURCHASE,
                      unit_cost=unit_cost, ref=ref, user=user)
        line.batch = batch
        line.save(update_fields=["batch", "updated_at"])
        if purchase.update_prices:
            medicine = line.medicine
            medicine.purchase_price = (line.pack_cost / line.conversion).quantize(COST)
            if line.sale_price > 0:
                medicine.sale_price = line.sale_price
            medicine.save()
    purchase.status = Purchase.Status.RECEIVED
    purchase.received_at = timezone.now()
    purchase.received_by = user
    purchase.save(update_fields=["status", "received_at", "received_by", "updated_at"])
    if purchase.total > 0:
        post_entry(purchase.supplier, date=purchase.date, kind=SupplierLedgerEntry.Kind.PURCHASE, credit=purchase.total, ref=ref)
    posting.post_purchase(purchase)
    return purchase


def delete_draft(purchase):
    if not purchase.is_draft:
        raise PurchaseError(_("Only drafts can be deleted."))
    purchase.delete()


def returnable(purchase):
    """[(line, units still returnable)] for a received purchase."""
    returned = dict(
        PurchaseReturnLine.objects.filter(purchase_line__purchase=purchase).values("purchase_line")
        .annotate(q=Sum("quantity")).values_list("purchase_line", "q")
    )
    return [(line, line.total_units - (returned.get(line.pk) or 0)) for line in purchase.lines.select_related("medicine", "batch", "medicine__sale_unit", "medicine__purchase_unit")]


@transaction.atomic
def create_return(purchase, quantities, *, date, reason="", user=None):
    """Send units back to the supplier: stock out from the original batch, owed amount reduced."""
    purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase.is_draft:
        raise PurchaseError(_("Only received purchases can be returned."))
    available = {line.pk: (line, left) for line, left in returnable(purchase)}
    picked = [(available[pk], qty) for pk, qty in quantities.items() if qty and pk in available]
    if not picked:
        raise PurchaseError(_("Enter how many units to return."))
    number = next_code(purchase.tenant, "purchase_return", prefix="PRT-", padding=5)
    purchase_return = PurchaseReturn.objects.create(number=number, purchase=purchase, supplier=purchase.supplier,
                                                    outlet=purchase.outlet, date=date, reason=reason, created_by=user)
    ref = ("purchase_return", purchase_return.pk, number)
    total = Decimal("0")
    for (line, left), qty in picked:
        if qty < 0 or qty > left:
            raise PurchaseError(_("%(name)s: you can return at most %(left)s.") % {"name": line.medicine.display_name, "left": left})
        unit_cost = line.unit_cost.quantize(COST)
        line_total = (unit_cost * qty).quantize(CENT, ROUND_HALF_UP)
        PurchaseReturnLine.objects.create(purchase_return=purchase_return, purchase_line=line, batch=line.batch,
                                          quantity=qty, unit_cost=unit_cost, line_total=line_total)
        try:
            post_movement(outlet=purchase.outlet, batch=line.batch, quantity=-qty, kind=StockMovement.Kind.PURCHASE_RETURN,
                          unit_cost=unit_cost, ref=ref, user=user, note=reason)
        except StockError as error:
            raise PurchaseError(str(error))
        total += line_total
    purchase_return.total = total
    purchase_return.save(update_fields=["total", "updated_at"])
    post_entry(purchase.supplier, date=date, kind=SupplierLedgerEntry.Kind.RETURN, debit=total, ref=ref,
               note=_("Return against %(number)s") % {"number": purchase.number})
    posting.post_purchase_return(purchase_return)
    return purchase_return
