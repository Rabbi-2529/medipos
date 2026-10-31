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

MODULES = (
    # "pos.create" = sell at the counter (and open/close your own cash register).
    Module("pos", _("POS (sell)"), SELLING, (CREATE,)),
    Module("sale", _("Sales"), SELLING, (LIST,)),
    Module("sale_return", _("Sale returns"), SELLING, (LIST, CREATE)),
    Module("register", _("Cash registers of all staff"), SELLING, (LIST,)),
    Module("customer", _("Customers"), SELLING, (LIST, CREATE, EDIT, DELETE)),
    Module("customer_receive", _("Due collection"), SELLING, (LIST, CREATE)),
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
    Module("payment_account", _("Payment accounts"), ACCOUNTS, (LIST, CREATE, EDIT, DELETE)),
    Module("employee", _("Employees"), PEOPLE, (LIST, CREATE, EDIT, DELETE)),
    Module("role", _("Roles & permissions"), PEOPLE, (LIST, CREATE, EDIT, DELETE)),
)

MODULES_BY_KEY = {m.key: m for m in MODULES}
ALL_CODES = frozenset(f"{m.key}.{a}" for m in MODULES for a in m.actions)
