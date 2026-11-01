"""Which records the audit log follows, and how.

Lines and ledgers the system writes for every document (sale lines, stock movements, journal lines of postings,
customer ledger entries) are left out: the document they belong to is logged, and they can be rebuilt from it."""
from dataclasses import dataclass

from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Rule:
    ignore: tuple = ()      # fields whose changes are never worth a line
    quiet: tuple = ()       # fields the system bumps itself (a save touching only these isn't logged)
    only_if: object = None  # instance -> bool: log only some rows (manual journal entries, credit purchases)
    m2m: tuple = ()         # many-to-many fields to follow
    link: str = ""          # URL name taking the record's pk
    creates: bool = True    # False: the system makes these (running numbers); only later edits are logged
    name: object = None     # instance -> what the log calls it (default str(instance))


def _title(instance):
    """One-per-pharmacy settings: call them by what they are."""
    return str(KINDS.get(instance._meta.label_lower, capfirst(str(instance._meta.verbose_name))))


DEFAULT = Rule()

AUDITED = {
    "accounts.user": Rule(ignore=("last_login", "date_joined", "language")),
    "items.medicine": Rule(link="items:edit"),
    "items.category": DEFAULT, "items.generic": DEFAULT, "items.manufacturer": DEFAULT, "items.unit": DEFAULT, "items.rack": DEFAULT,
    "customers.customer": Rule(ignore=("points",), link="customers:detail"),
    "customers.customerreceipt": DEFAULT,
    "suppliers.supplier": Rule(link="suppliers:detail"),
    "suppliers.supplierpayment": DEFAULT,
    "sales.sale": Rule(quiet=("cost_total", "points_earned"), link="sales:detail"),
    "sales.salereturn": DEFAULT,
    "sales.cashregister": DEFAULT,
    "sales.promotion": Rule(m2m=("medicines",), link="sales:promotion_edit"),
    "sales.quotation": DEFAULT,
    "purchases.purchase": Rule(link="purchases:detail"),
    "purchases.purchasereturn": DEFAULT,
    "inventory.transfer": DEFAULT, "inventory.writeoff": DEFAULT, "inventory.stockcount": DEFAULT,
    "accounting.paymentaccount": Rule(link="accounting:payment_account_edit"),
    "accounting.account": DEFAULT,
    "accounting.journalentry": Rule(only_if=lambda entry: not entry.source_type),  # manual entries; postings follow their document
    "accounting.cashentry": DEFAULT, "accounting.fundmovement": DEFAULT,
    "people.role": Rule(link="people:role_edit"),
    "people.employee": Rule(m2m=("outlets", "shifts"), link="people:employee_edit"),
    "outlets.outlet": Rule(link="outlets:edit"), "outlets.counter": DEFAULT,
    "setup.businesssettings": Rule(name=_title),
    "setup.sequence": Rule(quiet=("next_value",), creates=False),
    "hr.shift": DEFAULT, "hr.officenetwork": DEFAULT, "hr.attendance": DEFAULT, "hr.advance": DEFAULT,
    "hr.payroll": Rule(link="hr:payroll_detail"),
    "messaging.messagesettings": Rule(ignore=("last_daily_run",), name=_title),
    "messaging.loyaltyprogram": Rule(name=_title),
    "messaging.campaign": Rule(link="messaging:campaign"),
    "messaging.smscredit": Rule(only_if=lambda credit: credit.kind in ("purchase", "adjust"),
                                name=lambda credit: f"{credit.get_kind_display()} {credit.change:+d}"),
    "messaging.pointentry": Rule(only_if=lambda entry: entry.kind == "adjusted",
                                 name=lambda entry: f"{entry.customer.name} {entry.change:+d}"),
    "backup.exportlog": DEFAULT,
}

# Shown as dots, never stored.
SECRET = {"password", "sms_api_key", "whatsapp_api_key"}
NEVER = {"id", "tenant", "created_at", "updated_at"}


def rule_for(model):
    return AUDITED.get(model._meta.label_lower)


# What each kind of record is called on the audit pages (translated).
KINDS = {
    "accounts.user": _("Login"), "items.medicine": _("Medicine"), "items.category": _("Category"), "items.generic": _("Generic"),
    "items.manufacturer": _("Manufacturer"), "items.unit": _("Unit"), "items.rack": _("Rack"),
    "customers.customer": _("Customer"), "customers.customerreceipt": _("Due collection"),
    "suppliers.supplier": _("Supplier"), "suppliers.supplierpayment": _("Supplier payment"),
    "sales.sale": _("Sale"), "sales.salereturn": _("Sale return"), "sales.cashregister": _("Cash register"),
    "sales.promotion": _("Promotion"), "sales.quotation": _("Quotation"),
    "purchases.purchase": _("Purchase"), "purchases.purchasereturn": _("Purchase return"),
    "inventory.transfer": _("Stock transfer"), "inventory.writeoff": _("Damage / expired write-off"), "inventory.stockcount": _("Stock count"),
    "accounting.paymentaccount": _("Payment account"), "accounting.account": _("Ledger account"), "accounting.journalentry": _("Journal entry"),
    "accounting.cashentry": _("Expense / income"), "accounting.fundmovement": _("Deposit / transfer"),
    "people.role": _("Role"), "people.employee": _("Employee"), "outlets.outlet": _("Outlet"), "outlets.counter": _("Counter"),
    "setup.businesssettings": _("Business settings"), "setup.sequence": _("Document numbering"),
    "hr.shift": _("Shift"), "hr.officenetwork": _("Shop network"), "hr.attendance": _("Attendance"), "hr.advance": _("Advance"),
    "hr.payroll": _("Payroll"), "messaging.messagesettings": _("Message settings"), "messaging.loyaltyprogram": _("Loyalty settings"),
    "messaging.campaign": _("Campaign"), "messaging.smscredit": _("SMS credits"), "messaging.pointentry": _("Points correction"),
    "backup.exportlog": _("Data export"),
}
