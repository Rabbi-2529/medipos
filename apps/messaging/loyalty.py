"""Loyalty points: earned on bills, spent as money off a later bill, taken back when goods come back."""
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.customers.models import Customer

from .models import LoyaltyProgram, PointEntry

ZERO = Decimal("0")


class LoyaltyError(Exception):
    """A user-facing reason points can't be used or changed."""


def program():
    """This pharmacy's programme (made, switched off, the first time it is asked for)."""
    found = LoyaltyProgram.objects.first()
    return found if found is not None else LoyaltyProgram.objects.create()


def points_for(amount, prog):
    """Points a bill of `amount` earns."""
    if not prog.enabled or prog.earn_amount <= 0 or amount <= 0:
        return 0
    return int((amount / prog.earn_amount).to_integral_value(ROUND_DOWN))


def most_points(customer, bill, prog):
    """The most points this customer may spend on a bill of `bill` (their balance, capped by the programme's % limit)."""
    if not prog.enabled or customer is None or prog.point_value <= 0 or customer.points < max(prog.min_redeem, 1):
        return 0
    cap = bill * prog.max_redeem_percent / 100
    most = max(min(customer.points, int((cap / prog.point_value).to_integral_value(ROUND_DOWN))), 0)
    return most if most >= prog.min_redeem else 0  # a bill too small to spend the minimum on


def redeem_value(customer, points, bill, prog):
    """Money off for spending `points` on a bill of `bill`, or LoyaltyError."""
    if points <= 0:
        return ZERO
    if not prog.enabled:
        raise LoyaltyError(_("Loyalty points are switched off."))
    if customer is None:
        raise LoyaltyError(_("Pick the customer whose points are spent."))
    if points < prog.min_redeem:
        raise LoyaltyError(_("At least %(n)s points must be spent at a time.") % {"n": prog.min_redeem})
    # The row is locked until the sale commits, so two tills can't spend the same points.
    customer.points = Customer.objects.select_for_update().get(pk=customer.pk).points
    if points > customer.points:
        raise LoyaltyError(_("%(name)s only has %(n)s points.") % {"name": customer.name, "n": customer.points})
    allowed = most_points(customer, bill, prog)
    if not allowed:
        raise LoyaltyError(_("This bill is too small to spend points on."))
    if points > allowed:
        raise LoyaltyError(_("At most %(n)s points can be spent on this bill (%(percent)s%% of it).") % {
            "n": allowed, "percent": prog.max_redeem_percent})
    return (points * prog.point_value).quantize(Decimal("0.01"))


def _entry(customer, *, kind, change, date, sale=None, note="", user=None):
    """Move the customer's points by `change` and write it down. Locks the customer row so two tills can't race."""
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    customer.points += change
    customer.save(update_fields=["points", "updated_at"])
    return PointEntry.objects.create(customer=customer, date=date, kind=kind, change=change, balance=customer.points, sale=sale,
                                     note=note[:255], created_by=user)


def record_sale(sale, *, user=None):
    """After a sale is saved: take the points spent, add the points earned (on what was charged)."""
    if sale.customer_id is None:
        return
    prog = program()
    day = timezone.localdate(sale.sold_at)
    if sale.points_redeemed:
        _entry(sale.customer, kind=PointEntry.Kind.REDEEMED, change=-sale.points_redeemed, date=day, sale=sale, user=user,
               note=_("Spent on %(number)s") % {"number": sale.number})
    earned = points_for(sale.total, prog)
    if earned:
        _entry(sale.customer, kind=PointEntry.Kind.EARNED, change=earned, date=day, sale=sale, user=user,
               note=_("Earned on %(number)s") % {"number": sale.number})
        sale.points_earned = earned
        sale.save(update_fields=["points_earned", "updated_at"])


def on_return(sale_return, *, user=None):
    """Goods back: take back the share of the points that bill earned (as far as the customer still has them).
    Points spent on the bill are not given back."""
    sale = sale_return.sale
    if not sale.customer_id or not sale.points_earned or sale.total <= 0:
        return
    returned = sum(r.total for r in sale.returns.all())
    due_back = min(int((sale.points_earned * returned / sale.total).to_integral_value(ROUND_DOWN)), sale.points_earned)
    already = -sum(e.change for e in sale.point_entries.filter(kind=PointEntry.Kind.RETURNED))
    customer = Customer.objects.get(pk=sale.customer_id)
    take = min(due_back - already, max(customer.points, 0))
    if take > 0:
        _entry(customer, kind=PointEntry.Kind.RETURNED, change=-take, date=sale_return.date, sale=sale, user=user,
               note=_("Return %(number)s") % {"number": sale_return.number})


@transaction.atomic
def adjust(customer, change, *, note, user=None, date=None):
    """A manual correction (a gift, a mistake). Points can't go below zero."""
    if not change:
        raise LoyaltyError(_("Enter the points to add, or a minus number to take away."))
    if not (note or "").strip():
        raise LoyaltyError(_("Say why the points change."))
    current = Customer.objects.select_for_update().get(pk=customer.pk).points
    if current + change < 0:
        raise LoyaltyError(_("%(name)s only has %(n)s points.") % {"name": customer.name, "n": current})
    return _entry(customer, kind=PointEntry.Kind.ADJUSTED, change=change, date=date or timezone.localdate(), note=note.strip(),
                  user=user)
