"""Write-offs, transfers and stock counts. Stock itself only changes through stock.services.post_movement()."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounting import posting
from apps.setup.services import next_code
from apps.stock.models import Batch, BatchStock, StockMovement
from apps.stock.services import StockError, batch_on_hand, get_or_create_batch, post_movement

from .models import StockCount, StockCountLine, Transfer, TransferLine, WriteOff, WriteOffLine

CENT = Decimal("0.01")


class InventoryError(Exception):
    """A user-facing reason an inventory document was refused."""


def _whole(value, label):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise InventoryError(_("%(name)s: quantities must be whole numbers.") % {"name": label})
    if number < 0:
        raise InventoryError(_("%(name)s: quantities can't be negative.") % {"name": label})
    return number


def clean_out_lines(outlet, raw_lines):
    """[{batch, qty}] posted by a form -> [(batch, qty)] for stock leaving `outlet`; repeated batches are added up."""
    wanted = {}
    for raw in raw_lines or []:
        if not isinstance(raw, dict) or not str(raw.get("batch", "")).isdigit():
            continue
        batch_id = int(raw["batch"])
        qty = _whole(raw.get("qty") or 0, _("Line"))
        if qty:
            wanted[batch_id] = wanted.get(batch_id, 0) + qty
    if not wanted:
        raise InventoryError(_("Add at least one medicine with a quantity."))
    batches = Batch.objects.select_related("medicine").in_bulk(wanted)
    lines = []
    for batch_id, qty in wanted.items():
        batch = batches.get(batch_id)
        if batch is None:
            raise InventoryError(_("A batch on this form no longer exists. Reload and try again."))
        have = batch_on_hand(outlet, batch)
        if qty > have:
            raise InventoryError(_("%(name)s (batch %(batch)s): only %(have)s at %(outlet)s, %(qty)s entered.") % {
                "name": batch.medicine.display_name, "batch": batch, "have": have, "outlet": outlet, "qty": qty})
        lines.append((batch, qty))
    return lines


def _post(outlet, batch, quantity, kind, ref, user, note=""):
    try:
        return post_movement(outlet=outlet, batch=batch, quantity=quantity, kind=kind, ref=ref, user=user, note=note)
    except StockError as error:
        raise InventoryError(str(error))


# ---- Write-offs ------------------------------------------------------------------

@transaction.atomic
def create_write_off(*, tenant, outlet, date, reason, lines, note="", user=None):
    """Take damaged / expired / lost units out of stock. `lines` = [(batch, qty)] from clean_out_lines()."""
    number = next_code(tenant, "write_off", prefix="DMG-", padding=5)
    write_off = WriteOff.objects.create(number=number, outlet=outlet, date=date, reason=reason, note=note, created_by=user)
    ref = ("write_off", write_off.pk, number)
    total = Decimal("0")
    for batch, qty in lines:
        line_total = (batch.unit_cost * qty).quantize(CENT, ROUND_HALF_UP)
        WriteOffLine.objects.create(write_off=write_off, medicine_id=batch.medicine_id, batch=batch, quantity=qty,
                                    unit_cost=batch.unit_cost, line_total=line_total)
        _post(outlet, batch, -qty, StockMovement.Kind.DAMAGE, ref, user, note=write_off.get_reason_display())
        total += line_total
    write_off.total_cost = total
    write_off.save(update_fields=["total_cost", "updated_at"])
    posting.post_write_off(write_off)
    return write_off


def expired_stock(outlet, today=None):
    """Batches at `outlet` that may no longer be sold (expiry on or before today) and still have units."""
    today = today or timezone.localdate()
    return (BatchStock.objects.filter(outlet=outlet, quantity__gt=0, batch__expiry_date__lte=today)
            .select_related("batch", "medicine").order_by("batch__expiry_date"))


def write_off_expired(*, tenant, outlet, user=None, today=None):
    """One write-off for every expired unit at the outlet."""
    today = today or timezone.localdate()
    lines = [(stock.batch, stock.quantity) for stock in expired_stock(outlet, today)]
    if not lines:
        raise InventoryError(_("%(outlet)s has no expired stock.") % {"outlet": outlet})
    return create_write_off(tenant=tenant, outlet=outlet, date=today, reason=WriteOff.Reason.EXPIRED, lines=lines,
                            note=_("All expired stock"), user=user)


# ---- Transfers -------------------------------------------------------------------

@transaction.atomic
def dispatch_transfer(*, tenant, from_outlet, to_outlet, lines, note="", user=None):
    """Send stock: it leaves `from_outlet` now and is "in transit" until the other outlet receives it."""
    if from_outlet.pk == to_outlet.pk:
        raise InventoryError(_("Pick a different outlet to send to."))
    if not to_outlet.is_active:
        raise InventoryError(_("%(outlet)s is inactive.") % {"outlet": to_outlet})
    number = next_code(tenant, "transfer", prefix="TRF-", padding=5)
    transfer = Transfer.objects.create(number=number, from_outlet=from_outlet, to_outlet=to_outlet, note=note, dispatched_by=user)
    ref = ("transfer", transfer.pk, number)
    total = Decimal("0")
    for batch, qty in lines:
        TransferLine.objects.create(transfer=transfer, medicine_id=batch.medicine_id, batch=batch, quantity=qty, unit_cost=batch.unit_cost)
        _post(from_outlet, batch, -qty, StockMovement.Kind.TRANSFER_OUT, ref, user, note=_("To %(outlet)s") % {"outlet": to_outlet})
        total += batch.unit_cost * qty
    transfer.total_cost = total.quantize(CENT, ROUND_HALF_UP)
    transfer.save(update_fields=["total_cost", "updated_at"])
    return transfer


def _close_transfer(transfer, user, *, cancel):
    transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
    if not transfer.in_transit:
        raise InventoryError(_("%(number)s was already %(status)s.") % {
            "number": transfer.number, "status": transfer.get_status_display().lower()})
    outlet = transfer.from_outlet if cancel else transfer.to_outlet
    note = _("Cancelled, back from transit") if cancel else _("From %(outlet)s") % {"outlet": transfer.from_outlet}
    ref = ("transfer", transfer.pk, transfer.number)
    for line in transfer.lines.select_related("batch"):
        _post(outlet, line.batch, line.quantity, StockMovement.Kind.TRANSFER_IN, ref, user, note=note)
    transfer.status = Transfer.Status.CANCELLED if cancel else Transfer.Status.RECEIVED
    transfer.closed_at, transfer.closed_by = timezone.now(), user
    transfer.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])
    return transfer


@transaction.atomic
def receive_transfer(transfer, *, user=None):
    """Everything sent arrives at the receiving outlet, in the same batches."""
    return _close_transfer(transfer, user, cancel=False)


@transaction.atomic
def cancel_transfer(transfer, *, user=None):
    """Called off while still in transit: the units go back to the sending outlet."""
    return _close_transfer(transfer, user, cancel=True)


# ---- Stock counts ----------------------------------------------------------------

def clean_count_lines(outlet, raw_lines, raw_new):
    """Counted batches [{batch, counted}] and newly found ones [{medicine, batch_no, expiry, counted}]."""
    from apps.core.dates import parse_expiry
    from apps.items.models import Medicine

    counted = {}
    for raw in raw_lines or []:
        if not isinstance(raw, dict) or not str(raw.get("batch", "")).isdigit() or str(raw.get("counted", "")).strip() == "":
            continue
        counted[int(raw["batch"])] = _whole(raw["counted"], _("Line"))
    batches = Batch.objects.select_related("medicine").in_bulk(counted)
    lines = [(batches[pk], qty) for pk, qty in counted.items() if pk in batches]
    for raw in raw_new or []:
        if not isinstance(raw, dict) or not str(raw.get("medicine", "")).isdigit() or str(raw.get("counted", "")).strip() == "":
            continue
        medicine = Medicine.objects.filter(pk=raw["medicine"]).first()
        if medicine is None:
            continue
        qty = _whole(raw["counted"], medicine.display_name)
        expiry = None
        if str(raw.get("expiry", "")).strip():
            try:
                expiry = parse_expiry(raw["expiry"])
            except ValueError:
                raise InventoryError(_("%(name)s: couldn't read the expiry date.") % {"name": medicine.display_name})
        elif medicine.track_expiry:
            raise InventoryError(_("%(name)s: enter the expiry of the batch you found.") % {"name": medicine.display_name})
        batch_no = (raw.get("batch_no") or "").strip()[:40]
        batch = Batch.objects.filter(medicine=medicine, batch_no=batch_no, expiry_date=expiry).first()
        if batch is None:  # a batch the system never knew: value it at the medicine's purchase price
            unit_cost = (medicine.purchase_price / (medicine.conversion or 1)).quantize(Decimal("0.0001"))
            batch = get_or_create_batch(medicine, batch_no, expiry, unit_cost)
        lines.append((batch, qty))
    if not lines:
        raise InventoryError(_("Enter the counted quantity for at least one batch."))
    return lines


@transaction.atomic
def save_stock_count(*, tenant, outlet, date, lines, note="", user=None):
    """Record a count and move stock by the difference, so the system matches the shelf."""
    number = next_code(tenant, "stock_count", prefix="ADJ-", padding=5)
    count = StockCount.objects.create(number=number, outlet=outlet, date=date, note=note, created_by=user)
    ref = ("stock_count", count.pk, number)
    gain = loss = Decimal("0")
    for batch, counted in lines:
        system = batch_on_hand(outlet, batch)
        StockCountLine.objects.create(count=count, medicine_id=batch.medicine_id, batch=batch, system_quantity=system,
                                      counted_quantity=counted, unit_cost=batch.unit_cost)
        difference = counted - system
        if difference:
            _post(outlet, batch, difference, StockMovement.Kind.ADJUSTMENT, ref, user, note=note or _("Stock count"))
            if difference > 0:
                gain += batch.unit_cost * difference
            else:
                loss += batch.unit_cost * -difference
    count.gain_cost, count.loss_cost = gain.quantize(CENT, ROUND_HALF_UP), loss.quantize(CENT, ROUND_HALF_UP)
    count.save(update_fields=["gain_cost", "loss_cost", "updated_at"])
    posting.post_stock_count(count)
    return count
