"""Sidebar menu registry.

The whole planned menu lives here. An entry is shown only once its URL exists,
so each phase "switches on" its menus just by adding the views. Entries for
pharmacy screens are also filtered by permission: `perm` defaults to the
module's first action (e.g. "outlet.list") when `key` is a registered module.
"""
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from .permissions import MODULES_BY_KEY

TENANT = "tenant"
PLATFORM = "platform"
ALL = "all"

_DEFAULT = object()


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    url_name: str | None = None
    icon: str = "bi-circle"
    children: tuple = ()
    audience: str = TENANT
    perm: object = _DEFAULT

    @property
    def permission(self):
        if self.perm is not _DEFAULT:
            return self.perm
        module = MODULES_BY_KEY.get(self.key)
        return f"{module.key}.{module.default_action}" if module else None


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
        MenuItem("platform", _("Pharmacies"), icon="bi-buildings", audience=PLATFORM, children=(
            MenuItem("platform", _("Add pharmacy"), "platform:tenant_create", audience=PLATFORM),
            MenuItem("platform", _("Pharmacy list"), "platform:tenants", audience=PLATFORM),
        )),
        MenuItem("platform", _("Plans"), "platform:plans", "bi-card-checklist", audience=PLATFORM),
        MenuItem("platform", _("Medicine catalog"), "platform:catalog", "bi-journal-medical", audience=PLATFORM),
    )),
    MenuSection(_("Medicines & stock"), (
        MenuItem("item", _("Medicines"), icon="bi-capsule", perm=None, children=(
            MenuItem("item", _("Add medicine"), "items:create", perm="item.create"),
            MenuItem("item", _("Medicine list"), "items:list"),
            MenuItem("item", _("Add from catalog"), "items:catalog", perm="item.create"),
            MenuItem("item", _("Import from Excel"), "items:import", perm="item.create"),
            MenuItem("item", _("Bulk price update"), "items:bulk_price", perm="item.edit"),
            MenuItem("item", _("Barcode labels"), "items:labels"),
        )),
        MenuItem("item_setup", _("Medicine setup"), icon="bi-sliders", children=(
            _item("item_setup", _("Categories"), "items:categories"),
            _item("item_setup", _("Generics"), "items:generics"),
            _item("item_setup", _("Manufacturers"), "items:manufacturers"),
            _item("item_setup", _("Units"), "items:units"),
            _item("item_setup", _("Racks"), "items:racks"),
        )),
        MenuItem("stock", _("Stock"), icon="bi-box-seam", perm=None, children=(
            _item("stock", _("Current stock"), "stock:list"),
            _item("stock", _("Low stock"), "stock:low"),
            _item("stock", _("Expiring soon"), "stock:expiring"),
            _item("stock", _("Adjustments"), "stock:adjustments"),
            MenuItem("stock", _("Opening stock"), "stock:opening", perm="stock.create"),
        )),
    )),
    MenuSection(_("Sales & customers"), (
        MenuItem("pos", _("POS"), "sales:pos", "bi-cart3"),
        MenuItem("sale", _("Sales"), icon="bi-receipt", perm=None, children=(
            _item("sale", _("Sale list"), "sales:list"),
            _item("sale_return", _("Sale returns"), "sales:returns"),
            MenuItem("pos", _("Cash registers"), "sales:registers"),
            _item("quotation", _("Quotations"), "sales:quotations"),
        )),
        MenuItem("customer", _("Customers"), icon="bi-people", perm=None, children=(
            _item("customer", _("Customer list"), "customers:list"),
            MenuItem("customer_receive", _("Collect due"), "customers:receipt_create", perm="customer_receive.create"),
            _item("customer_receive", _("Collections"), "customers:receipts"),
        )),
    )),
    MenuSection(_("Purchases & suppliers"), (
        MenuItem("purchase", _("Purchases"), icon="bi-bag-check", perm=None, children=(
            MenuItem("purchase", _("Add purchase"), "purchases:create", perm="purchase.create"),
            _item("purchase", _("Purchase list"), "purchases:list"),
            _item("purchase_return", _("Purchase returns"), "purchases:returns"),
        )),
        MenuItem("supplier", _("Suppliers"), icon="bi-truck", perm=None, children=(
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
    MenuSection(_("Reports"), (
        MenuItem("report", _("Reports"), "reports:index", "bi-bar-chart-line"),
    )),
    MenuSection(_("Shop setup"), (
        MenuItem("settings", _("Business settings"), "setup:settings", "bi-gear"),
        MenuItem("outlet", _("Outlets"), icon="bi-shop", perm=None, children=(
            _item("outlet", _("Outlet list"), "outlets:list"),
            _item("counter", _("Counters"), "outlets:counters"),
        )),
        MenuItem("subscription", _("Subscription"), "billing:subscription", "bi-credit-card"),
    )),
)

# What a pharmacy whose subscription lapsed can still open.
LOCKED_KEYS = frozenset({"subscription"})


def _audience_ok(item, user):
    if item.audience == ALL:
        return True
    if item.audience == PLATFORM:
        return user.is_platform_admin
    return user.tenant_id is not None


def _resolve(item, user, current_view, codes):
    if not _audience_ok(item, user):
        return None
    if item.audience == TENANT and item.permission is not None and item.permission not in codes:
        return None
    children = [c for c in (_resolve(ch, user, current_view, codes) for ch in item.children) if c]
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
    from apps.people.access import user_permissions

    match = getattr(request, "resolver_match", None)
    current_view = match.view_name if match else ""
    tenant = getattr(request, "tenant", None)
    locked = tenant is not None and not tenant.has_access
    codes = user_permissions(request.user)
    sections = []
    for section in MENU:
        items = [i for i in (_resolve(it, request.user, current_view, codes) for it in section.items) if i]
        if locked:
            items = [i for i in items if i["key"] in LOCKED_KEYS]
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
