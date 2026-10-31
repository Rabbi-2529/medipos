from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.setup.services import next_code

from .models import CustomerLedgerEntry, CustomerReceipt

ZERO = Decimal("0")


class CustomerError(Exception):
    """A user-facing reason a customer transaction was refused."""


def post_entry(customer, *, date, kind, debit=ZERO, credit=ZERO, ref=None, note=""):
    ref_type, ref_id, ref_label = ref or ("", None, "")
    return CustomerLedgerEntry.objects.create(customer=customer, date=date, kind=kind, debit=debit, credit=credit,
                                              ref_type=ref_type, ref_id=ref_id, ref_label=ref_label, note=note[:255])


def balance(customer):
    """What the customer owes the pharmacy (negative = advance/credit in their favour)."""
    totals = customer.ledger.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (totals["d"] or ZERO) - (totals["c"] or ZERO)


def with_balances(queryset):
    money = DecimalField(max_digits=14, decimal_places=2)

    def total(field):
        rows = (CustomerLedgerEntry.objects.filter(customer=OuterRef("pk")).order_by()
                .values("customer").annotate(t=Sum(field)).values("t"))
        return Coalesce(Subquery(rows, output_field=money), Value(ZERO), output_field=money)

    return queryset.annotate(balance=total("debit") - total("credit"))


@transaction.atomic
def set_opening_balance(customer):
    entry = customer.ledger.filter(kind=CustomerLedgerEntry.Kind.OPENING).first()
    amount = customer.opening_balance or ZERO
    if amount == 0:
        if entry:
            entry.delete()
        return
    if entry is None:
        entry = CustomerLedgerEntry(customer=customer, kind=CustomerLedgerEntry.Kind.OPENING,
                                    date=timezone.localdate(customer.created_at))
    entry.debit, entry.credit = (amount, ZERO) if amount > 0 else (ZERO, -amount)
    entry.ref_label = _("Opening due")
    entry.save()


def can_take_due(customer, extra):
    """None if the customer may owe `extra` more, else a message."""
    if customer.credit_limit is None:
        return None
    owed = balance(customer)
    if owed + extra > customer.credit_limit:
        return _("%(name)s may owe at most %(limit)s and already owes %(owed)s.") % {
            "name": customer.name, "limit": customer.credit_limit, "owed": owed}
    return None


def sale_due(sale):
    """Still unpaid on a sale: total − paid at counter − later collections − returns not refunded in cash."""
    collected = sale.receipts.aggregate(t=Sum("amount"))["t"] or ZERO
    returns = sale.returns.aggregate(t=Sum("total"), r=Sum("refund_amount"))
    credited = (returns["t"] or ZERO) - (returns["r"] or ZERO)
    return max(ZERO, sale.total - sale.paid - collected - credited)


@transaction.atomic
def collect(customer, *, amount, account, date, sale=None, outlet=None, register=None, reference="", note="", user=None):
    if amount <= 0:
        raise CustomerError(_("Amount must be more than zero."))
    if sale is not None:
        if sale.customer_id != customer.pk:
            raise CustomerError(_("That sale belongs to another customer."))
        due = sale_due(sale)
        if amount > due:
            raise CustomerError(_("Only %(due)s is due on %(number)s.") % {"due": due, "number": sale.number})
    number = next_code(customer.tenant, "customer_receipt", prefix="RCV-", padding=5)
    receipt = CustomerReceipt.objects.create(number=number, customer=customer, sale=sale, outlet=outlet, register=register,
                                             date=date, amount=amount, account=account, reference=reference, note=note,
                                             created_by=user)
    post_entry(customer, date=date, kind=CustomerLedgerEntry.Kind.PAYMENT, credit=amount, ref=("customer_receipt", receipt.pk, number),
               note=(_("For %(sale)s") % {"sale": sale.number}) if sale else note)
    return receipt


def ledger_with_running_balance(customer):
    running, rows = ZERO, []
    for entry in customer.ledger.all():
        running += entry.debit - entry.credit
        rows.append((entry, running))
    return rows
