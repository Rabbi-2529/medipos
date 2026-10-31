"""Figures for the accounting reports, all read from journal lines."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from .models import Account, JournalLine

ZERO = Decimal("0")
CENT = Decimal("0.01")

# What each kind of document is called in reports, and the page that shows it.
SOURCES = {
    "sale": (_("Sales"), "sales:detail"),
    "sale_return": (_("Sale returns"), None),
    "customer_receipt": (_("Due collected from customers"), None),
    "customer_opening": (_("Customers' opening dues"), "customers:detail"),
    "purchase": (_("Purchases"), "purchases:detail"),
    "purchase_return": (_("Purchase returns"), None),
    "supplier_payment": (_("Paid to suppliers"), None),
    "supplier_opening": (_("Suppliers' opening balances"), "suppliers:detail"),
    "opening_stock": (_("Opening stock"), None),
    "write_off": (_("Write-offs"), "inventory:damage"),
    "stock_count": (_("Stock counts"), "inventory:count"),
    "payment_account_opening": (_("Opening balances of accounts"), None),
    "income": (_("Other income"), None),
    "expense": (_("Expenses"), None),
    "fund_movement": (_("Deposits, withdrawals & transfers"), None),
    "advance": (_("Staff advances"), None),
    "payroll": (_("Payroll"), "hr:payroll_detail"),
    "": (_("Manual journal entries"), None),
}


def source_label(source_type):
    return SOURCES.get(source_type, (source_type,))[0]


def sums(*, start=None, end=None, outlet=None, accounts=None):
    """{account_id: (debits, credits)} for lines dated within [start, end]."""
    lines = JournalLine.objects.all()
    if start:
        lines = lines.filter(entry__date__gte=start)
    if end:
        lines = lines.filter(entry__date__lte=end)
    if outlet:
        lines = lines.filter(entry__outlet=outlet)
    if accounts is not None:
        lines = lines.filter(account__in=accounts)
    # Rounded to paisa: SQLite hands back sums of decimals with float noise.
    return {r["account"]: (Decimal(r["d"] or 0).quantize(CENT), Decimal(r["c"] or 0).quantize(CENT))
            for r in lines.values("account").annotate(d=Sum("debit"), c=Sum("credit"))}


def natural(account, debit, credit):
    """Balance on the account's normal side: assets/expenses debit minus credit, the rest the other way round."""
    return debit - credit if account.debit_normal else credit - debit


def _accounts(ids=None, types=None):
    accounts = Account.objects.select_related("payment_account")
    if ids is not None:
        accounts = accounts.filter(pk__in=ids)
    if types:
        accounts = accounts.filter(type__in=types)
    return list(accounts.order_by("code"))


def balance_of(account, *, end=None, start=None):
    debit, credit = sums(start=start, end=end, accounts=[account]).get(account.pk, (ZERO, ZERO))
    return natural(account, debit, credit)


def trial_balance(as_of):
    totals = sums(end=as_of)
    rows, debit_total, credit_total = [], ZERO, ZERO
    for account in _accounts(totals):
        net = totals[account.pk][0] - totals[account.pk][1]
        if not net:
            continue
        rows.append({"account": account, "debit": max(net, ZERO), "credit": max(-net, ZERO)})
        debit_total += max(net, ZERO)
        credit_total += max(-net, ZERO)
    return {"rows": rows, "debit": debit_total, "credit": credit_total, "balanced": debit_total == credit_total}


def profit_and_loss(start, end, outlet=None):
    totals = sums(start=start, end=end, outlet=outlet)

    def amount(account):
        return natural(account, *totals.get(account.pk, (ZERO, ZERO)))

    accounts = _accounts(types=[Account.Type.INCOME, Account.Type.EXPENSE])
    by_key = {a.system_key: a for a in accounts if a.system_key}
    sales = amount(by_key["sales"]) if "sales" in by_key else ZERO
    returns = -amount(by_key["sales_returns"]) if "sales_returns" in by_key else ZERO  # a debit balance
    cogs = amount(by_key["cogs"]) if "cogs" in by_key else ZERO
    other_income = [(a, amount(a)) for a in accounts if a.type == Account.Type.INCOME and a.system_key not in ("sales", "sales_returns")]
    expenses = [(a, amount(a)) for a in accounts if a.type == Account.Type.EXPENSE and a.system_key != "cogs"]
    other_income = [row for row in other_income if row[1]]
    expenses = [row for row in expenses if row[1]]
    net_sales = sales - returns
    gross = net_sales - cogs
    income_total, expense_total = sum((v for _a, v in other_income), ZERO), sum((v for _a, v in expenses), ZERO)
    return {
        "sales": sales, "returns": returns, "net_sales": net_sales, "cogs": cogs, "gross": gross,
        "gross_margin": (gross / net_sales * 100).quantize(Decimal("0.1")) if net_sales else None,
        "other_income": other_income, "other_income_total": income_total, "expenses": expenses, "expense_total": expense_total,
        "net": gross + income_total - expense_total,
    }


def balance_sheet(as_of):
    totals = sums(end=as_of)
    sections = defaultdict(list)
    profit = ZERO
    for account in _accounts(totals):
        value = natural(account, *totals[account.pk])
        if account.type == Account.Type.INCOME:
            profit += value
        elif account.type == Account.Type.EXPENSE:
            profit -= value
        elif value:
            sections[account.type].append((account, value))
    assets = sum((v for _a, v in sections[Account.Type.ASSET]), ZERO)
    liabilities = sum((v for _a, v in sections[Account.Type.LIABILITY]), ZERO)
    equity = sum((v for _a, v in sections[Account.Type.EQUITY]), ZERO) + profit
    return {
        "assets": sections[Account.Type.ASSET], "liabilities": sections[Account.Type.LIABILITY], "equity": sections[Account.Type.EQUITY],
        "profit": profit, "asset_total": assets, "liability_total": liabilities, "equity_total": equity,
        "liabilities_and_equity": liabilities + equity,
        "balanced": assets == liabilities + equity,
    }


def cash_flow(start, end):
    """Money in and out of the payment accounts, by what caused it."""
    money = _accounts(types=[Account.Type.ASSET])
    money = [a for a in money if a.payment_account_id]
    ids = [a.pk for a in money]
    before = sums(end=_day_before(start), accounts=ids) if start else {}
    opening = sum((before.get(pk, (ZERO, ZERO))[0] - before.get(pk, (ZERO, ZERO))[1] for pk in ids), ZERO)
    lines = JournalLine.objects.filter(account__in=ids, entry__date__gte=start, entry__date__lte=end)
    flows = defaultdict(lambda: [ZERO, ZERO])
    for row in lines.values("entry__source_type").annotate(d=Sum("debit"), c=Sum("credit")):
        flows[row["entry__source_type"]][0] += Decimal(row["d"] or 0).quantize(CENT)
        flows[row["entry__source_type"]][1] += Decimal(row["c"] or 0).quantize(CENT)
    rows = sorted(({"label": source_label(k), "in": v[0], "out": v[1], "net": v[0] - v[1]} for k, v in flows.items()),
                  key=lambda r: -abs(r["net"]))
    money_in, money_out = sum((r["in"] for r in rows), ZERO), sum((r["out"] for r in rows), ZERO)
    per_account = []
    period = sums(start=start, end=end, accounts=ids)
    for account in money:
        open_ = before.get(account.pk, (ZERO, ZERO))
        moved = period.get(account.pk, (ZERO, ZERO))
        per_account.append({"account": account, "opening": open_[0] - open_[1], "in": moved[0], "out": moved[1],
                            "closing": open_[0] - open_[1] + moved[0] - moved[1]})
    return {"rows": rows, "opening": opening, "in": money_in, "out": money_out, "net": money_in - money_out,
            "closing": opening + money_in - money_out,
            "accounts": per_account}


def statement(account, start, end):
    """Opening balance, every line in the period with a running balance, and the closing balance."""
    opening = balance_of(account, end=_day_before(start)) if start else ZERO
    running, rows = opening, []
    lines = (JournalLine.objects.filter(account=account, entry__date__gte=start, entry__date__lte=end)
             .select_related("entry").order_by("entry__date", "entry__id", "id"))
    for line in lines:
        running += natural(account, line.debit, line.credit)
        rows.append({"line": line, "balance": running})
    return {"opening": opening, "rows": rows, "closing": running,
            "debit": sum((r["line"].debit for r in rows), ZERO), "credit": sum((r["line"].credit for r in rows), ZERO)}


def _day_before(day):
    from datetime import timedelta

    return day - timedelta(days=1)
