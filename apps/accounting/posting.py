"""Double-entry journals for every document that moves money or stock.

Each document has at most one journal entry (source_type + source_id). Posting again replaces it,
so `rebuild()` can re-create the books from the documents at any time.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.setup.services import next_code

from .chart import ensure_chart, money_account, system_account
from .models import CashEntry, FundMovement, JournalEntry, JournalLine

ZERO = Decimal("0")
CENT = Decimal("0.01")


class JournalError(Exception):
    """A journal that doesn't balance, or a manual entry that can't be saved."""


def _cents(value):
    return Decimal(value or 0).quantize(CENT, ROUND_HALF_UP)


@transaction.atomic
def post(source, *, date, lines, memo="", outlet=None, user=None):
    """Save one balanced entry for `source` = (type, id, label), replacing the document's earlier entry.

    `lines` = [(account, debit, credit)]; zero lines are dropped and lines on the same account merged.
    """
    source_type, source_id, source_label = source
    JournalEntry.objects.filter(source_type=source_type, source_id=source_id).delete()
    merged = {}
    for account, debit, credit in lines:
        net = merged.get(account.pk, (account, ZERO))[1] + _cents(debit) - _cents(credit)
        merged[account.pk] = (account, net)
    rows = [(account, net) for account, net in merged.values() if net]
    if not rows:
        return None
    if sum(net for _a, net in rows) != 0:
        raise JournalError(_("%(source)s doesn't balance by %(diff)s.") % {"source": source_label, "diff": sum(n for _a, n in rows)})
    entry = JournalEntry.objects.create(number=next_code(_tenant(), "journal", prefix="JV-", padding=6), date=date, memo=memo[:255],
                                        source_type=source_type, source_id=source_id, source_label=source_label[:40],
                                        outlet=outlet, created_by=user)
    JournalLine.objects.bulk_create([JournalLine(entry=entry, account=account, debit=max(net, ZERO), credit=max(-net, ZERO),
                                                 tenant_id=entry.tenant_id) for account, net in rows])
    return entry


def unpost(source_type, source_id):
    JournalEntry.objects.filter(source_type=source_type, source_id=source_id).delete()


def _tenant():
    from apps.core.tenancy import get_current_tenant

    return get_current_tenant()


def _local(moment):
    return timezone.localdate(moment)


# ---- Sales side ---------------------------------------------------------------------

def post_sale(sale):
    """Money in (or owed by the customer) against sales and VAT; stock out at cost."""
    lines = [(money_account(p.account), p.amount, 0) for p in sale.payments.select_related("account")]
    lines.append((system_account("receivable"), sale.total - sale.paid, 0))
    lines += [(system_account("sales"), 0, sale.total - sale.vat_amount), (system_account("vat"), 0, sale.vat_amount)]
    cost = _cents(sale.cost_total)
    lines += [(system_account("cogs"), cost, 0), (system_account("inventory"), 0, cost)]
    return post(("sale", sale.pk, sale.number), date=_local(sale.sold_at), lines=lines, outlet=sale.outlet,
                memo=_("Sale %(number)s") % {"number": sale.number}, user=sale.created_by)


def _movement_cost(ref_type, ref_id):
    from apps.stock.models import StockMovement

    total = StockMovement.objects.filter(ref_type=ref_type, ref_id=ref_id).aggregate(t=Sum(F("quantity") * F("unit_cost")))["t"]
    return _cents(abs(total or 0))


def post_sale_return(sale_return):
    """Sales reversed; the refund leaves a payment account and the rest comes off the customer's due; stock back at cost."""
    refund = sale_return.refund_amount
    lines = [(system_account("sales_returns"), sale_return.total, 0), (system_account("receivable"), 0, sale_return.total - refund)]
    if refund:
        lines.append((money_account(sale_return.refund_account), 0, refund))
    cost = _movement_cost("sale_return", sale_return.pk)
    lines += [(system_account("inventory"), cost, 0), (system_account("cogs"), 0, cost)]
    return post(("sale_return", sale_return.pk, sale_return.number), date=sale_return.date, lines=lines, outlet=sale_return.outlet,
                memo=_("Return %(number)s on %(sale)s") % {"number": sale_return.number, "sale": sale_return.sale.number},
                user=sale_return.created_by)


def post_customer_receipt(receipt):
    return post(("customer_receipt", receipt.pk, receipt.number), date=receipt.date, outlet=receipt.outlet, user=receipt.created_by,
                lines=[(money_account(receipt.account), receipt.amount, 0), (system_account("receivable"), 0, receipt.amount)],
                memo=_("Due collected from %(name)s") % {"name": receipt.customer.name})


def post_customer_opening(customer):
    amount = customer.opening_balance or ZERO
    if not amount:
        return unpost("customer_opening", customer.pk)
    return post(("customer_opening", customer.pk, customer.name), date=_local(customer.created_at),
                lines=[(system_account("receivable"), amount, 0), (system_account("opening"), 0, amount)],
                memo=_("Opening due of %(name)s") % {"name": customer.name})


# ---- Buying side --------------------------------------------------------------------

def post_purchase(purchase):
    """Stock in, owed to the supplier (payments are their own entries)."""
    return post(("purchase", purchase.pk, purchase.number), date=purchase.date, outlet=purchase.outlet, user=purchase.received_by,
                lines=[(system_account("inventory"), purchase.total, 0), (system_account("payable"), 0, purchase.total)],
                memo=_("Purchase %(number)s from %(supplier)s") % {"number": purchase.number, "supplier": purchase.supplier.name})


def post_purchase_return(purchase_return):
    return post(("purchase_return", purchase_return.pk, purchase_return.number), date=purchase_return.date, outlet=purchase_return.outlet,
                lines=[(system_account("payable"), purchase_return.total, 0), (system_account("inventory"), 0, purchase_return.total)],
                memo=_("Returned to %(supplier)s") % {"supplier": purchase_return.supplier.name}, user=purchase_return.created_by)


def post_supplier_payment(payment):
    return post(("supplier_payment", payment.pk, payment.number), date=payment.date, user=payment.created_by,
                lines=[(system_account("payable"), payment.amount, 0), (money_account(payment.account), 0, payment.amount)],
                memo=_("Paid to %(supplier)s") % {"supplier": payment.supplier.name})


def post_supplier_opening(supplier):
    amount = supplier.opening_balance or ZERO
    if not amount:
        return unpost("supplier_opening", supplier.pk)
    return post(("supplier_opening", supplier.pk, supplier.name), date=_local(supplier.created_at),
                lines=[(system_account("opening"), amount, 0), (system_account("payable"), 0, amount)],
                memo=_("Opening balance owed to %(name)s") % {"name": supplier.name})


# ---- Stock --------------------------------------------------------------------------

def post_opening_stock(outlet):
    """All opening stock of an outlet as one entry (re-posted after every import)."""
    from apps.stock.models import StockMovement

    movements = StockMovement.objects.filter(outlet=outlet, kind=StockMovement.Kind.OPENING)
    value = _cents(movements.aggregate(t=Sum(F("quantity") * F("unit_cost")))["t"])
    first = movements.order_by("created_at").first()
    if first is None or not value:
        return unpost("opening_stock", outlet.pk)
    return post(("opening_stock", outlet.pk, outlet.name), date=_local(first.created_at), outlet=outlet,
                lines=[(system_account("inventory"), value, 0), (system_account("opening"), 0, value)],
                memo=_("Opening stock at %(outlet)s") % {"outlet": outlet.name})


def post_write_off(write_off):
    return post(("write_off", write_off.pk, write_off.number), date=write_off.date, outlet=write_off.outlet, user=write_off.created_by,
                lines=[(system_account("stock_loss"), write_off.total_cost, 0), (system_account("inventory"), 0, write_off.total_cost)],
                memo=_("Written off: %(reason)s") % {"reason": write_off.get_reason_display()})


def post_stock_count(count):
    net = count.gain_cost - count.loss_cost
    return post(("stock_count", count.pk, count.number), date=count.date, outlet=count.outlet, user=count.created_by,
                lines=[(system_account("inventory"), net, 0), (system_account("stock_loss"), 0, net)],
                memo=_("Stock count %(number)s") % {"number": count.number})


# ---- Money --------------------------------------------------------------------------

def post_payment_account_opening(payment_account):
    amount = payment_account.opening_balance or ZERO
    if not amount:
        return unpost("payment_account_opening", payment_account.pk)
    return post(("payment_account_opening", payment_account.pk, payment_account.name), date=_local(payment_account.created_at),
                lines=[(money_account(payment_account), amount, 0), (system_account("opening"), 0, amount)],
                memo=_("Opening balance of %(name)s") % {"name": payment_account.name})


def post_cash_entry(entry):
    money = money_account(entry.payment_account)
    lines = ([(money, entry.amount, 0), (entry.account, 0, entry.amount)] if entry.kind == CashEntry.Kind.INCOME
             else [(entry.account, entry.amount, 0), (money, 0, entry.amount)])
    return post((entry.kind, entry.pk, entry.number), date=entry.date, lines=lines, outlet=entry.outlet, user=entry.created_by,
                memo=entry.note or entry.account.label)


def post_fund_movement(movement):
    amount = movement.amount
    if movement.kind == FundMovement.Kind.TRANSFER:
        lines = [(money_account(movement.to_account), amount, 0), (money_account(movement.from_account), 0, amount)]
    elif movement.kind == FundMovement.Kind.DEPOSIT:
        lines = [(money_account(movement.to_account), amount, 0), (system_account("capital"), 0, amount)]
    else:
        lines = [(system_account("drawings"), amount, 0), (money_account(movement.from_account), 0, amount)]
    return post(("fund_movement", movement.pk, movement.number), date=movement.date, lines=lines, user=movement.created_by,
                memo=movement.note or movement.get_kind_display())


# ---- Staff ----------------------------------------------------------------------------

def post_advance(advance):
    """Money lent to staff: it is still ours (an asset) until payroll takes it back."""
    return post(("advance", advance.pk, advance.number), date=advance.date, user=advance.created_by,
                lines=[(system_account("advances"), advance.amount, 0), (money_account(advance.payment_account), 0, advance.amount)],
                memo=_("Advance to %(name)s") % {"name": advance.employee.display_name})


def post_payroll(payroll):
    """Salaries, commission and bonus as an expense; advances taken back; the rest paid out."""
    totals = payroll.totals()
    return post(("payroll", payroll.pk, payroll.number), date=payroll.paid_on, user=payroll.paid_by,
                lines=[(system_account("salaries"), totals["gross"], 0), (system_account("advances"), 0, totals["advance"]),
                       (money_account(payroll.payment_account), 0, totals["net"])],
                memo=_("Salaries for %(month)s") % {"month": date_format(payroll.month, "F Y")})


# ---- Rebuilding ---------------------------------------------------------------------

@transaction.atomic
def rebuild():
    """Throw away every automatic entry of the current pharmacy and post them again from the documents."""
    from apps.customers.models import Customer, CustomerReceipt
    from apps.hr.models import Advance, Payroll
    from apps.inventory.models import StockCount, WriteOff
    from apps.outlets.models import Outlet
    from apps.purchases.models import Purchase, PurchaseReturn
    from apps.sales.models import Sale, SaleReturn
    from apps.suppliers.models import Supplier, SupplierPayment

    from .models import PaymentAccount

    ensure_chart()
    JournalEntry.objects.exclude(source_type="").delete()
    count = 0
    for queryset, poster in (
        (PaymentAccount.objects.all(), post_payment_account_opening),
        (Customer.objects.all(), post_customer_opening),
        (Supplier.objects.all(), post_supplier_opening),
        (Outlet.objects.all(), post_opening_stock),
        (Purchase.objects.filter(status=Purchase.Status.RECEIVED).select_related("supplier"), post_purchase),
        (PurchaseReturn.objects.select_related("supplier"), post_purchase_return),
        (SupplierPayment.objects.select_related("supplier", "account"), post_supplier_payment),
        (Sale.objects.all(), post_sale),
        (SaleReturn.objects.select_related("sale", "refund_account"), post_sale_return),
        (CustomerReceipt.objects.select_related("customer", "account"), post_customer_receipt),
        (WriteOff.objects.all(), post_write_off),
        (StockCount.objects.all(), post_stock_count),
        (CashEntry.objects.select_related("account", "payment_account"), post_cash_entry),
        (FundMovement.objects.select_related("from_account", "to_account"), post_fund_movement),
        (Advance.objects.select_related("employee", "payment_account"), post_advance),
        (Payroll.objects.filter(status=Payroll.Status.PAID).select_related("payment_account"), post_payroll),
    ):
        for document in queryset.order_by("pk"):
            if poster(document) is not None:
                count += 1
    return count
