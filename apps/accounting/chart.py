"""The chart of accounts every pharmacy starts with.

System accounts are the ones the app posts to by itself; their names are shown in the reader's
language. The default expense and income categories are ordinary accounts, created in the
pharmacy's own language so they can be renamed like any other.
"""
from django.utils import translation
from django.utils.translation import gettext
from django.utils.translation import gettext_noop as N_

from .models import Account, PaymentAccount

A, L, E, I, X = Account.Type.ASSET, Account.Type.LIABILITY, Account.Type.EQUITY, Account.Type.INCOME, Account.Type.EXPENSE

SYSTEM_ACCOUNTS = {
    # key: (code, name, type)
    "receivable": ("1200", N_("Customers owe us (receivable)"), A),
    "inventory": ("1300", N_("Medicine stock (inventory)"), A),
    "payable": ("2100", N_("We owe suppliers (payable)"), L),
    "vat": ("2200", N_("VAT payable"), L),
    "capital": ("3000", N_("Owner's capital"), E),
    "opening": ("3100", N_("Opening balances"), E),
    "drawings": ("3200", N_("Owner's drawings"), E),
    "sales": ("4000", N_("Medicine sales"), I),
    "sales_returns": ("4010", N_("Sales returns"), I),
    "other_income": ("4900", N_("Other income"), I),
    "cogs": ("5000", N_("Cost of medicines sold"), X),
    "stock_loss": ("5100", N_("Stock loss & adjustments"), X),
}

DEFAULT_EXPENSES = [
    ("6000", N_("Shop rent")), ("6010", N_("Salaries & wages")), ("6020", N_("Electricity, water & gas")),
    ("6030", N_("Internet & phone")), ("6040", N_("Transport & delivery")), ("6050", N_("Repairs & maintenance")),
    ("6060", N_("Shop supplies & packaging")), ("6070", N_("Bank & mobile banking charges")), ("6080", N_("Licence & fees")),
    ("6900", N_("Other expenses")),
]
DEFAULT_INCOME = [("4910", N_("Supplier discounts & bonus")), ("4920", N_("Commission received"))]


# System accounts the app fills by itself; they are never offered as an expense or income category.
NOT_CATEGORIES = ("sales", "sales_returns", "cogs", "stock_loss")
# Where new categories get their codes: expenses 6000–8999, other income 4910–4999.
CATEGORY_CODES = {X: ("6000", "8999"), I: ("4910", "4999")}


def category_accounts(account_type):
    """Expense or income categories people can pick and manage (active and inactive)."""
    return Account.objects.filter(type=account_type).exclude(system_key__in=NOT_CATEGORIES)


def next_category_code(account_type):
    """The next round code after the last category, e.g. 6900 -> 6910."""
    low, high = CATEGORY_CODES[account_type]
    codes = [int(c) for c in Account.objects.filter(code__gte=low, code__lte=high).values_list("code", flat=True) if c.isdigit()]
    return _free_code(str(max(codes) // 10 * 10 + 10 if codes else int(low)))


def system_label(key):
    return gettext(SYSTEM_ACCOUNTS[key][1]) if key in SYSTEM_ACCOUNTS else None


def system_account(key):
    """The system account `key` of the current pharmacy (created on first use)."""
    code, name, kind = SYSTEM_ACCOUNTS[key]
    account = Account.objects.filter(system_key=key).first()
    if account is None:
        account = Account.objects.create(system_key=key, code=_free_code(code), name=name, type=kind)
    return account


def money_account(payment_account):
    """The ledger account behind a cash drawer, bank account or wallet (codes 1100–1199)."""
    account = Account.objects.filter(payment_account=payment_account).first()
    if account is None:
        account = Account.objects.create(payment_account=payment_account, code=_free_code("1100"), name=payment_account.name, type=A)
    return account


def _free_code(start):
    """`start`, or the next unused code after it."""
    code = int(start)
    taken = set(Account.objects.values_list("code", flat=True))
    while str(code) in taken:
        code += 1
    return str(code)


def ensure_chart(language=None):
    """Create whatever is missing of the starting chart for the current pharmacy. Safe to run again."""
    for key in SYSTEM_ACCOUNTS:
        system_account(key)
    for payment_account in PaymentAccount.objects.all():
        money_account(payment_account)
    if not Account.objects.filter(type__in=[X, I], system_key="").exists():
        with translation.override(language or translation.get_language()):
            for code, name in DEFAULT_EXPENSES:
                Account.objects.create(code=_free_code(code), name=gettext(name), type=X)
            for code, name in DEFAULT_INCOME:
                Account.objects.create(code=_free_code(code), name=gettext(name), type=I)
