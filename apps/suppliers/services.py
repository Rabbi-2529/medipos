from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.setup.services import next_code

from .models import SupplierLedgerEntry, SupplierPayment

ZERO = Decimal("0")


class SupplierError(Exception):
    """A user-facing reason a supplier transaction was refused."""


def post_entry(supplier, *, date, kind, debit=ZERO, credit=ZERO, ref=None, note=""):
    ref_type, ref_id, ref_label = ref or ("", None, "")
    return SupplierLedgerEntry.objects.create(
        supplier=supplier, date=date, kind=kind, debit=debit, credit=credit,
        ref_type=ref_type, ref_id=ref_id, ref_label=ref_label, note=note[:255],
    )


def balance(supplier):
    """What the pharmacy owes this supplier (negative = supplier owes the pharmacy)."""
    totals = supplier.ledger.aggregate(c=Sum("credit"), d=Sum("debit"))
    return (totals["c"] or ZERO) - (totals["d"] or ZERO)


def with_balances(queryset):
    """Annotate `balance` without multiplying rows (subquery per supplier)."""
    money = DecimalField(max_digits=14, decimal_places=2)

    def total(field):
        rows = (SupplierLedgerEntry.objects.filter(supplier=OuterRef("pk")).order_by()
                .values("supplier").annotate(t=Sum(field)).values("t"))
        return Coalesce(Subquery(rows, output_field=money), Value(ZERO), output_field=money)

    return queryset.annotate(balance=total("credit") - total("debit"))


@transaction.atomic
def set_opening_balance(supplier):
    """Keep the single opening-balance ledger line in step with supplier.opening_balance."""
    entry = supplier.ledger.filter(kind=SupplierLedgerEntry.Kind.OPENING).first()
    amount = supplier.opening_balance or ZERO
    if amount == 0:
        if entry:
            entry.delete()
        return
    if entry is None:
        entry = SupplierLedgerEntry(supplier=supplier, kind=SupplierLedgerEntry.Kind.OPENING,
                                    date=timezone.localdate(supplier.created_at))
    entry.credit, entry.debit = (amount, ZERO) if amount > 0 else (ZERO, -amount)
    entry.ref_label = _("Opening balance")
    entry.save()


@transaction.atomic
def record_payment(*, supplier, amount, account, date, purchase=None, reference="", note="", user=None):
    if amount <= 0:
        raise SupplierError(_("Amount must be more than zero."))
    if purchase is not None:
        if purchase.supplier_id != supplier.pk:
            raise SupplierError(_("That purchase belongs to another supplier."))
        if purchase.is_draft:
            raise SupplierError(_("Receive the purchase before paying for it."))
        due = purchase.due_amount()
        if amount > due:
            raise SupplierError(_("Only %(due)s is due on %(number)s.") % {"due": due, "number": purchase.number})
    number = next_code(supplier.tenant, "supplier_payment", prefix="SPY-", padding=5)
    payment = SupplierPayment.objects.create(
        number=number, supplier=supplier, purchase=purchase, date=date, amount=amount, account=account,
        reference=reference, note=note, created_by=user,
    )
    post_entry(supplier, date=date, kind=SupplierLedgerEntry.Kind.PAYMENT, debit=amount,
               ref=("supplier_payment", payment.pk, number),
               note=(_("For %(purchase)s") % {"purchase": purchase.number}) if purchase else note)
    return payment


def ledger_with_running_balance(supplier):
    running = ZERO
    rows = []
    for entry in supplier.ledger.all():
        running += entry.credit - entry.debit
        rows.append((entry, running))
    return rows
