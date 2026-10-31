"""Roles every new pharmacy starts with.

Codes for modules that don't exist yet are stored too: they switch on by
themselves when their phase registers the module (Role.codes ignores the rest).
"""
from django.utils.translation import gettext_noop

from apps.core.permissions import ALL_CODES, CRUD

_LATER_MODULES = (
    "item", "item_setup", "stock", "pos", "sale", "sale_return", "quotation", "customer", "customer_receive",
    "purchase", "purchase_return", "supplier", "supplier_payment", "transfer", "damage", "adjustment", "promotion",
    "income", "expense", "deposit_withdraw", "accounting", "report", "profit_report",
)


def _manager_codes():
    now = {c for c in ALL_CODES if not c.startswith(("role.", "subscription."))}
    later = {f"{m}.{a}" for m in _LATER_MODULES for a in CRUD}
    return sorted(now | later)


DEFAULT_ROLES = {
    "manager": {
        "name": gettext_noop("Manager"),
        "description": gettext_noop("Runs the shop day to day: everything except roles and the subscription."),
        "permissions": _manager_codes(),
    },
    "cashier": {
        "name": gettext_noop("Cashier"),
        "description": gettext_noop("Sells at the counter, takes returns and due payments."),
        "permissions": sorted([
            "pos.create", "pos.list", "quotation.list", "quotation.create",
            "sale.list", "sale.show", "sale_return.list", "sale_return.create",
            "customer.list", "customer.show", "customer.create", "customer_receive.create",
            "item.list", "item.show", "stock.list",
        ]),
    },
}
