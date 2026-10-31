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

MODULES = (
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
