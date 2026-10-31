"""The only code that changes stock. Every change locks the row, refuses to go negative and leaves a movement."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from .models import Batch, BatchStock, StockMovement


class StockError(Exception):
    """A user-facing reason a stock change was refused (e.g. not enough stock)."""


def get_or_create_batch(medicine, batch_no, expiry_date, unit_cost=None):
    """The batch for (medicine, batch no., expiry); a new purchase refreshes its unit cost."""
    batch, created = Batch.objects.get_or_create(
        medicine=medicine, batch_no=(batch_no or "").strip()[:40], expiry_date=expiry_date,
        defaults={"unit_cost": unit_cost or Decimal("0")},
    )
    if not created and unit_cost is not None and batch.unit_cost != unit_cost:
        batch.unit_cost = unit_cost
        batch.save(update_fields=["unit_cost", "updated_at"])
    return batch


@transaction.atomic
def post_movement(*, outlet, batch, quantity, kind, unit_cost=None, ref=None, user=None, note=""):
    """Add (quantity > 0) or take away (quantity < 0) sale units of `batch` at `outlet`.

    `ref` is (ref_type, ref_id, ref_label) of the document causing the change.
    """
    if quantity == 0:
        raise StockError(_("Quantity can't be zero."))
    stock, _created = BatchStock.objects.select_for_update().get_or_create(
        outlet=outlet, batch=batch, defaults={"medicine_id": batch.medicine_id, "quantity": 0}
    )
    new_quantity = stock.quantity + quantity
    if new_quantity < 0:
        raise StockError(_("Not enough stock of %(medicine)s (batch %(batch)s) at %(outlet)s: %(have)s left, %(want)s needed.") % {
            "medicine": batch.medicine.display_name, "batch": batch, "outlet": outlet, "have": stock.quantity, "want": -quantity,
        })
    stock.quantity = new_quantity
    stock.save(update_fields=["quantity", "updated_at"])
    ref_type, ref_id, ref_label = ref or ("", None, "")
    return StockMovement.objects.create(
        outlet=outlet, batch=batch, medicine_id=batch.medicine_id, kind=kind, quantity=quantity,
        unit_cost=batch.unit_cost if unit_cost is None else unit_cost, balance_after=new_quantity,
        ref_type=ref_type, ref_id=ref_id, ref_label=ref_label[:40], note=note[:255], created_by=user,
    )


def on_hand(outlet, medicine):
    """Total sale units of a medicine at an outlet."""
    return BatchStock.objects.filter(outlet=outlet, medicine=medicine).aggregate(q=Sum("quantity"))["q"] or 0


def batch_on_hand(outlet, batch):
    return BatchStock.objects.filter(outlet=outlet, batch=batch).values_list("quantity", flat=True).first() or 0


def available_batches(outlet, medicine):
    """Batches with stock at an outlet, earliest expiry first (FEFO); undated batches last."""
    return (BatchStock.objects.filter(outlet=outlet, medicine=medicine, quantity__gt=0)
            .select_related("batch").order_by("batch__expiry_date", "batch__id"))


def format_quantity(units, medicine):
    """205 pieces of a box-of-100 medicine -> "2 Box + 5 Piece"; otherwise "205 Piece"."""
    units = units or 0
    sale = medicine.sale_unit.name
    if medicine.conversion > 1 and abs(units) >= medicine.conversion:
        packs, rest = divmod(abs(units), medicine.conversion)
        sign = "-" if units < 0 else ""
        text = f"{sign}{packs} {medicine.purchase_unit.name}"
        return f"{text} + {rest} {sale}" if rest else text
    return f"{units} {sale}"
