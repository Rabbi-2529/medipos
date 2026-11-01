"""Permission registry: every module a role can be granted, and the actions it supports.

A permission code is "<module>.<action>", e.g. "outlet.create". Each phase appends
its modules here; the roles screen and the sidebar pick them up automatically.
"""
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

LIST, SHOW, CREATE, EDIT, DELETE = "list", "show", "create", "edit", "delete"
CRUD = (LIST, SHOW, CREATE, EDIT, DELETE)

ACTION_LABELS = {
    LIST: _("List"),
    SHOW: _("View"),
    CREATE: _("Add"),
    EDIT: _("Edit"),
    DELETE: _("Delete"),
}


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    group: str
    actions: tuple = CRUD

    @property
    def default_action(self):
        return self.actions[0]


SHOP = _("Shop setup")
PEOPLE = _("People")
ACCOUNTS = _("Accounts")
MEDICINES = _("Medicines")
BUYING = _("Stock & purchases")
SELLING = _("Sales & customers")
INVENTORY = _("Inventory control")
REPORTS = _("Reports")

MODULES = (
    # "pos.create" = sell at the counter (and open/close your own cash register).
    Module("pos", _("POS (sell)"), SELLING, (CREATE,)),
    Module("sale", _("Sales"), SELLING, (LIST,)),
    Module("sale_return", _("Sale returns"), SELLING, (LIST, CREATE)),
    Module("register", _("Cash registers of all staff"), SELLING, (LIST,)),
    Module("customer", _("Customers"), SELLING, (LIST, CREATE, EDIT, DELETE)),
    Module("customer_receive", _("Due collection"), SELLING, (LIST, CREATE)),
    Module("promotion", _("Promotions"), SELLING, (LIST, CREATE, EDIT, DELETE)),
    # "quotation.create" also loads a quotation into the POS (selling it still needs "pos.create").
    Module("quotation", _("Quotations"), SELLING, (LIST, CREATE, DELETE)),
    # "message.create" = send bills, due reminders, tests and campaigns; "message.edit" = gateways and automatic messages.
    Module("message", _("SMS, email & campaigns"), SELLING, (LIST, CREATE, EDIT)),
    # "loyalty.edit" = the programme's rules and correcting a customer's points.
    Module("loyalty", _("Loyalty points"), SELLING, (LIST, EDIT)),
    # "transfer.create" = send stock to another outlet and receive what was sent to yours (or cancel it).
    Module("transfer", _("Stock transfers"), INVENTORY, (LIST, CREATE)),
    Module("damage", _("Damage & expired write-offs"), INVENTORY, (LIST, CREATE)),
    Module("adjustment", _("Stock counts & adjustments"), INVENTORY, (LIST, CREATE)),
    # "stock.create" = opening-stock import. "purchase.create" also receives purchases; edit/delete apply to drafts.
    Module("stock", _("Stock"), BUYING, (LIST, CREATE)),
    Module("purchase", _("Purchases"), BUYING, (LIST, CREATE, EDIT, DELETE)),
    Module("purchase_return", _("Purchase returns"), BUYING, (LIST, CREATE)),
    Module("supplier", _("Suppliers"), BUYING, (LIST, CREATE, EDIT, DELETE)),
    Module("supplier_payment", _("Supplier payments"), BUYING, (LIST, CREATE)),
    # "item.create" also covers Excel import and adding from the catalog; "item.edit" covers bulk price changes.
    Module("item", _("Medicines"), MEDICINES, (LIST, CREATE, EDIT, DELETE)),
    Module("item_setup", _("Medicine setup lists"), MEDICINES, (LIST, CREATE, EDIT, DELETE)),
    Module("settings", _("Business settings"), SHOP, (SHOW, EDIT)),
    Module("outlet", _("Outlets"), SHOP, (LIST, CREATE, EDIT, DELETE)),
    Module("counter", _("Counters"), SHOP, (LIST, CREATE, EDIT, DELETE)),
    Module("subscription", _("Subscription"), SHOP, (SHOW,)),
    # Downloads everything (customers, sales, staff): only the owner has it unless they give it in a role.
    Module("backup", _("Export & backup data"), SHOP, (CREATE,)),
    Module("payment_account", _("Payment accounts"), ACCOUNTS, (LIST, CREATE, EDIT, DELETE)),
    Module("expense", _("Expenses"), ACCOUNTS, (LIST, CREATE, DELETE)),
    Module("income", _("Other income"), ACCOUNTS, (LIST, CREATE, DELETE)),
    Module("deposit_withdraw", _("Deposits, withdrawals & transfers"), ACCOUNTS, (LIST, CREATE, DELETE)),
    # "accounting.list" = the books and reports; "create" = manual journal entries; "edit" = the chart of accounts.
    Module("accounting", _("Accounting books & reports"), ACCOUNTS, (LIST, CREATE, EDIT)),
    # "report.list" = sales, stock, ledger and staff reports. Profit reports show costs, so they have their own switch.
    Module("report", _("Reports"), REPORTS, (LIST,)),
    Module("profit_report", _("Profit reports (costs & margins)"), REPORTS, (LIST,)),
    Module("employee", _("Employees"), PEOPLE, (LIST, CREATE, EDIT, DELETE)),
    Module("role", _("Roles & permissions"), PEOPLE, (LIST, CREATE, EDIT, DELETE)),
    # "attendance.create" = mark the daily sheet; "attendance.edit" = shifts and the shop's networks. "salary.create" = set pay on employees, give advances, prepare and pay payrolls.
    Module("attendance", _("Attendance"), PEOPLE, (LIST, CREATE, EDIT)),
    Module("salary", _("Payroll & advances"), PEOPLE, (LIST, CREATE)),
)

MODULES_BY_KEY = {m.key: m for m in MODULES}
ALL_CODES = frozenset(f"{m.key}.{a}" for m in MODULES for a in m.actions)
