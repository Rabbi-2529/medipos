"""Income and expense vouchers, deposits/withdrawals/transfers, and manual journal entries."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.translation import gettext as _

from apps.setup.services import next_code

from . import posting
from .chart import NOT_CATEGORIES, category_accounts, next_category_code
from .models import Account, CashEntry, FundMovement, JournalEntry, JournalLine

ZERO = Decimal("0")


class AccountingError(Exception):
    """A user-facing reason a voucher or journal entry was refused."""


def _amount(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise AccountingError(_("Enter a valid amount."))
    if amount <= 0:
        raise AccountingError(_("Amount must be more than zero."))
    return amount


@transaction.atomic
def record_cash_entry(*, tenant, kind, date, account, payment_account, amount, outlet=None, register=None, reference="", note="", user=None):
    """An expense paid from (or other income received into) a payment account, under a category account."""
    wanted = Account.Type.INCOME if kind == CashEntry.Kind.INCOME else Account.Type.EXPENSE
    if account.type != wanted or account.system_key in NOT_CATEGORIES:
        raise AccountingError(_("Pick an %(type)s category.") % {"type": Account.Type(wanted).label.lower()})
    prefix = "INC-" if kind == CashEntry.Kind.INCOME else "EXP-"
    entry = CashEntry.objects.create(kind=kind, number=next_code(tenant, kind, prefix=prefix, padding=5), date=date, account=account,
                                     payment_account=payment_account, amount=_amount(amount), outlet=outlet, register=register,
                                     reference=reference, note=note, created_by=user)
    posting.post_cash_entry(entry)
    return entry


def _category_type(kind):
    return Account.Type.INCOME if kind == CashEntry.Kind.INCOME else Account.Type.EXPENSE


def _check_name(kind, name, exclude=None):
    name = (name or "").strip()
    if not name:
        raise AccountingError(_("Enter a name."))
    clash = category_accounts(_category_type(kind)).exclude(pk=getattr(exclude, "pk", None))
    if any(a.label.casefold() == name.casefold() for a in clash):
        raise AccountingError(_("“%(name)s” already exists.") % {"name": name})
    return name[:100]


@transaction.atomic
def add_category(kind, name, description=""):
    """A new expense or income category: an account in the chart with the next free code."""
    account_type = _category_type(kind)
    return Account.objects.create(code=next_category_code(account_type), name=_check_name(kind, name), type=account_type,
                                  description=(description or "")[:255])


def rename_category(account, kind, name, description=""):
    if account.is_locked:
        raise AccountingError(_("This category is kept by the app and can't be renamed."))
    account.name, account.description = _check_name(kind, name, exclude=account), (description or "")[:255]
    account.save(update_fields=["name", "description", "updated_at"])
    return account


@transaction.atomic
def delete_cash_entry(entry):
    posting.unpost(entry.kind, entry.pk)
    entry.delete()


@transaction.atomic
def move_funds(*, tenant, kind, date, amount, from_account=None, to_account=None, register=None, note="", user=None):
    """Move money between payment accounts, or record the owner putting money in / taking it out."""
    if kind == FundMovement.Kind.TRANSFER:
        if not (from_account and to_account):
            raise AccountingError(_("Choose both accounts."))
        if from_account == to_account:
            raise AccountingError(_("Pick two different accounts."))
    elif kind == FundMovement.Kind.DEPOSIT:
        if not to_account:
            raise AccountingError(_("Choose the account the money goes into."))
        from_account = None
    else:
        if not from_account:
            raise AccountingError(_("Choose the account the money comes out of."))
        to_account = None
    movement = FundMovement.objects.create(kind=kind, number=next_code(tenant, "fund_movement", prefix="FND-", padding=5), date=date,
                                           from_account=from_account, to_account=to_account, amount=_amount(amount),
                                           register=register, note=note, created_by=user)
    posting.post_fund_movement(movement)
    return movement


@transaction.atomic
def delete_fund_movement(movement):
    posting.unpost("fund_movement", movement.pk)
    movement.delete()


@transaction.atomic
def create_manual_entry(*, tenant, date, memo, lines, user=None):
    """A journal entry typed by an accountant: [(account, debit, credit, memo)], at least two lines, balanced."""
    clean = []
    for account, debit, credit, line_memo in lines:
        debit, credit = Decimal(debit or 0).quantize(Decimal("0.01")), Decimal(credit or 0).quantize(Decimal("0.01"))
        if debit < 0 or credit < 0:
            raise AccountingError(_("Amounts can't be negative."))
        if debit and credit:
            raise AccountingError(_("A line is either a debit or a credit, not both."))
        if debit or credit:
            clean.append((account, debit, credit, line_memo))
    if len(clean) < 2:
        raise AccountingError(_("A journal entry needs at least two lines."))
    debits, credits = sum(l[1] for l in clean), sum(l[2] for l in clean)
    if debits != credits:
        raise AccountingError(_("Debits (%(debits)s) and credits (%(credits)s) must be equal.") % {"debits": debits, "credits": credits})
    entry = JournalEntry.objects.create(number=next_code(tenant, "journal", prefix="JV-", padding=6), date=date, memo=memo[:255], created_by=user)
    for account, debit, credit, line_memo in clean:
        JournalLine.objects.create(entry=entry, account=account, debit=debit, credit=credit, memo=(line_memo or "")[:255])
    return entry


@transaction.atomic
def delete_manual_entry(entry):
    if not entry.is_manual:
        raise AccountingError(_("Entries made by documents change only with their document."))
    entry.delete()
