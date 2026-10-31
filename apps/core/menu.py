"""Sidebar menu registry.

The whole planned menu lives here. An entry is shown only once its URL exists,
so each phase "switches on" its menus just by adding the views. `key` is the
permission module the roles matrix (Phase 2) will check.
"""
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

TENANT = "tenant"
PLATFORM = "platform"
ALL = "all"


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    url_name: str | None = None
    icon: str = "bi-circle"
    children: tuple = ()
    audience: str = TENANT


@dataclass(frozen=True)
class MenuSection:
    title: str
    items: tuple


def _item(key, label, url_name):
    return MenuItem(key, label, url_name)


MENU = (
    MenuSection("", (
        MenuItem("dashboard", _("Dashboard"), "core:dashboard", "bi-speedometer2", audience=ALL),
    )),
    MenuSection(_("Platform"), (
        MenuItem("platform", _("Pharmacies"), "platform:tenants", "bi-buildings", audience=PLATFORM),
        MenuItem("platform", _("Plans"), "platform:plans", "bi-card-checklist", audience=PLATFORM),
    )),
    MenuSection(_("Medicines & stock"), (
        MenuItem("item", _("Medicines"), icon="bi-capsule", children=(
            _item("item", _("Add medicine"), "items:create"),
            _item("item", _("Medicine list"), "items:list"),
            _item("item", _("Bulk import"), "items:import"),
        )),
        MenuItem("item_setup", _("Medicine setup"), icon="bi-sliders", children=(
            _item("item_setup", _("Categories"), "items:categories"),
            _item("item_setup", _("Generics"), "items:generics"),
            _item("item_setup", _("Manufacturers"), "items:manufacturers"),
            _item("item_setup", _("Units"), "items:units"),
            _item("item_setup", _("Racks"), "items:racks"),
        )),
        MenuItem("stock", _("Stock"), icon="bi-box-seam", children=(
            _item("stock", _("Current stock"), "stock:list"),
            _item("stock", _("Low stock"), "stock:low"),
            _item("stock", _("Expiring soon"), "stock:expiring"),
            _item("stock", _("Adjustments"), "stock:adjustments"),
        )),
    )),
    MenuSection(_("Sales & customers"), (
        MenuItem("pos", _("POS"), "sales:pos", "bi-cart3"),
        MenuItem("sale", _("Sales"), icon="bi-receipt", children=(
            _item("sale", _("Sale list"), "sales:list"),
            _item("sale_return", _("Sale returns"), "sales:returns"),
            _item("quotation", _("Quotations"), "sales:quotations"),
        )),
        MenuItem("customer", _("Customers"), icon="bi-people", children=(
            _item("customer", _("Customer list"), "customers:list"),
            _item("customer_receive", _("Due collection"), "customers:receipts"),
        )),
    )),
    MenuSection(_("Purchases & suppliers"), (
        MenuItem("purchase", _("Purchases"), icon="bi-bag-check", children=(
            _item("purchase", _("Add purchase"), "purchases:create"),
            _item("purchase", _("Purchase list"), "purchases:list"),
            _item("purchase_return", _("Purchase returns"), "purchases:returns"),
        )),
        MenuItem("supplier", _("Suppliers"), icon="bi-truck", children=(
            _item("supplier", _("Supplier list"), "suppliers:list"),
            _item("supplier_payment", _("Supplier payments"), "suppliers:payments"),
        )),
    )),
    MenuSection(_("Inventory control"), (
        MenuItem("transfer", _("Transfers"), "inventory:transfers", "bi-arrow-left-right"),
        MenuItem("damage", _("Damage & expired"), "inventory:damages", "bi-trash3"),
    )),
    MenuSection(_("Accounts"), (
        MenuItem("payment_account", _("Payment accounts"), "accounting:payment_accounts", "bi-wallet2"),
        MenuItem("expense", _("Income & expenses"), icon="bi-cash-stack", children=(
            _item("income", _("Income"), "accounting:incomes"),
            _item("expense", _("Expenses"), "accounting:expenses"),
            _item("deposit_withdraw", _("Deposit / withdraw"), "accounting:transfers"),
        )),
        MenuItem("accounting", _("Accounting reports"), "accounting:reports", "bi-journal-text"),
    )),
    MenuSection(_("People"), (
        MenuItem("employee", _("Employees"), "people:employees", "bi-person-badge"),
        MenuItem("role", _("Roles & permissions"), "people:roles", "bi-shield-lock"),
        MenuItem("attendance", _("Attendance"), "people:attendance", "bi-calendar-check"),
        MenuItem("salary", _("Payroll"), "people:payroll", "bi-cash-coin"),
    )),
    MenuSection(_("Reports & settings"), (
        MenuItem("report", _("Reports"), "reports:index", "bi-bar-chart-line"),
        MenuItem("outlet", _("Outlets"), "setup:outlets", "bi-shop"),
        MenuItem("settings", _("Settings"), "setup:settings", "bi-gear"),
    )),
)


def _audience_ok(item, user):
    if item.audience == ALL:
        return True
    if item.audience == PLATFORM:
        return user.is_platform_admin
    return user.tenant_id is not None


def _resolve(item, user, current_view):
    if not _audience_ok(item, user):
        return None
    children = [c for c in (_resolve(ch, user, current_view) for ch in item.children) if c]
    url = None
    if item.url_name:
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            url = None
    if url is None and not children:
        return None
    return {
        "key": item.key,
        "label": item.label,
        "icon": item.icon,
        "url": url,
        "children": children,
        "active": item.url_name == current_view or any(c["active"] for c in children),
    }


def build_menu(request):
    match = getattr(request, "resolver_match", None)
    current_view = match.view_name if match else ""
    sections = []
    for section in MENU:
        items = [i for i in (_resolve(it, request.user, current_view) for it in section.items) if i]
        if items:
            sections.append({"title": section.title, "items": items})
    return sections


def flatten_menu(sections):
    """Linkable entries for the Ctrl+K search."""
    out = []
    for section in sections:
        for item in section["items"]:
            if item["url"]:
                out.append({"label": str(item["label"]), "url": item["url"], "group": str(section["title"])})
            for child in item["children"]:
                out.append({"label": str(child["label"]), "url": child["url"], "group": str(item["label"])})
    return out
