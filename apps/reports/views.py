from datetime import date
from decimal import Decimal

from django.http import Http404
from django.shortcuts import render
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.spreadsheets import xlsx_response
from apps.customers.models import Customer
from apps.items.models import Medicine
from apps.outlets.models import Outlet
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_shop_date, get_business_settings
from apps.suppliers.models import Supplier

from . import other_reports as o
from . import sales_reports as s
from .engine import GROUPS, ROW_LIMIT, Filters, Link

REPORTS = [
    s.DailySales(), s.SalesList(), s.SaleItems(), s.ProductSales(), s.DueSales(), s.PaymentMethods(),
    s.ProductProfit(), s.InvoiceProfit(), s.PriceHistory(),
    o.Purchases(), o.PurchaseItems(), o.StockValue(), o.ItemTracking(),
    o.CustomerBalances(), o.CustomerLedger(), o.SupplierBalances(), o.SupplierLedger(),
    o.EmployeeSales(), o.Registers(), o.StaffAttendance(), o.SalariesPaid(),
    o.MessagesSent(), o.LoyaltyPoints(),
]
BY_KEY = {r.key: r for r in REPORTS}


def _visible(request):
    return [r for r in REPORTS if has_perm(request.user, r.permission)]


@shop_permission_required(("report.list", "profit_report.list", "attendance.list", "salary.list", "message.list", "loyalty.list"))
def index(request):
    reports = _visible(request)
    groups = [{"key": key, "label": label, "icon": icon, "tone": tone, "reports": [r for r in reports if r.group == key]}
              for key, label, icon, tone in GROUPS]
    return render(request, "reports/index.html", {"groups": [g for g in groups if g["reports"]]})


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _pk(queryset, value):
    return queryset.filter(pk=value).first() if str(value or "").isdigit() else None


def _filters(request, report):
    """Read the report's filters from the address bar; anything not allowed or not found is ignored."""
    get = request.GET
    start, end = report.default_period()
    start, end = _date(get.get("start")) or start, _date(get.get("end")) or end
    if start > end:
        start, end = end, start
    allowed = [o.pk for o in request.allowed_outlets]
    outlet = next((o for o in request.allowed_outlets if str(o.pk) == get.get("outlet")), None)
    return Filters(
        start=start, end=end, outlet=outlet, outlet_ids=[outlet.pk] if outlet else allowed,
        customer=_pk(Customer.objects, get.get("customer")), supplier=_pk(Supplier.objects, get.get("supplier")),
        medicine=_pk(Medicine.objects, get.get("medicine")), category=(get.get("category") or "").strip(),
        cashier=_pk(User.objects.filter(tenant=request.tenant), get.get("cashier")),  # logins aren't tenant-scoped by their manager
        group=get.get("group", ""), option=get.get("option", report.options[0][0] if report.options else ""),
    )


def _plain(value):
    """A cell as Excel wants it: numbers as numbers, links as their text."""
    if isinstance(value, Link):
        return value.text
    if isinstance(value, Decimal):
        return float(value)
    return "" if value is None else value


def report_view(request, key):
    report = BY_KEY.get(key)
    if report is None:
        raise Http404

    @shop_permission_required(report.permission)
    def view(request):
        f = _filters(request, report)
        missing = report.needs and not getattr(f, report.needs)
        result = None if missing else report.run(f)
        if result is not None and request.GET.get("export") == "xlsx":
            headers = [str(c.label) for c in result.columns]
            rows = [[_plain(row.get(c.key)) for c in result.columns] for row in result.rows]
            if result.totals:
                rows.append([str(_("Total")) if i == 0 else _plain(result.totals.get(c.key, "")) for i, c in enumerate(result.columns)])
            return xlsx_response(f"{report.key}-{f.start:%Y%m%d}-{f.end:%Y%m%d}.xlsx", headers, rows)
        settings = get_business_settings(request.tenant)
        cashiers = User.objects.filter(tenant=request.tenant, is_active=True).order_by("first_name", "email") if "cashier" in report.filters else []
        return render(request, "reports/report.html", {
            "report": report, "f": f, "result": result, "missing": missing,
            "rows": result.rows[:ROW_LIMIT] if result else [], "cut": result is not None and len(result.rows) > ROW_LIMIT, "limit": ROW_LIMIT,
            "outlets": request.allowed_outlets, "cashiers": cashiers, "auto_print": request.GET.get("print") == "1",
            "period": f"{format_shop_date(f.start, settings)} – {format_shop_date(f.end, settings)}",
            "query": request.GET.urlencode(),
        })

    return view(request)
