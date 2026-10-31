"""Figures for the pharmacy dashboard. Each block appears only for people allowed to see it."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.formats import date_format

from apps.people.access import has_perm

ZERO = Decimal("0")
VALUE = DecimalField(max_digits=18, decimal_places=4)
CHART_DAYS = 30


def dashboard_context(request):
    from apps.sales.models import Sale, SaleLine, SalePayment, SaleReturn

    from .sales_reports import due_of, with_dues

    user, today = request.user, timezone.localdate()
    month_start = today.replace(day=1)
    outlet_ids = [o.pk for o in request.allowed_outlets]
    ctx = {}
    if has_perm(user, "sale.list"):
        sales = Sale.objects.filter(outlet_id__in=outlet_ids)
        day = sales.filter(sold_at__date=today).aggregate(n=Count("id"), total=Sum("total"))
        month_sales = sales.filter(sold_at__date__gte=month_start)
        month = month_sales.aggregate(n=Count("id"), total=Sum("total"), vat=Sum("vat_amount"), cost=Sum("cost_total"))
        month_returns = SaleReturn.objects.filter(outlet_id__in=outlet_ids, date__gte=month_start)
        returns = month_returns.aggregate(t=Sum("total"))["t"] or ZERO
        returns_today = month_returns.filter(date=today).aggregate(t=Sum("total"))["t"] or ZERO
        ctx["kpi"] = {  # both net of returns
            "today": (day["total"] or ZERO) - returns_today, "today_count": day["n"], "month": (month["total"] or ZERO) - returns,
            "month_count": month["n"],
            "avg_bill": ((month["total"] or ZERO) / month["n"]).quantize(Decimal("0.01")) if month["n"] else ZERO,
        }
        if has_perm(user, "profit_report.list"):
            revenue = (month["total"] or ZERO) - (month["vat"] or ZERO)
            ctx["kpi"]["profit"] = (revenue - Decimal(month["cost"] or 0)).quantize(Decimal("0.01"))
            ctx["kpi"]["margin"] = (ctx["kpi"]["profit"] / revenue * 100).quantize(Decimal("0.1")) if revenue else None

        start = today - timedelta(days=CHART_DAYS - 1)
        by_day = {r["day"]: r for r in sales.filter(sold_at__date__gte=start).annotate(day=TruncDate("sold_at"))
                  .values("day").annotate(total=Sum("total"), n=Count("id"))}
        ctx["chart"] = [{
            "date": d.isoformat(), "label": date_format(d, "j M"), "long": date_format(d, "D, j M"),
            "total": float(by_day.get(d, {}).get("total") or 0), "count": by_day.get(d, {}).get("n", 0),
        } for d in (start + timedelta(days=i) for i in range(CHART_DAYS))]

        top = (SaleLine.objects.filter(sale__in=month_sales).values("medicine").annotate(value=Sum("line_total"), units=Sum("quantity"))
               .order_by("-value")[:5])
        from apps.items.models import Medicine

        names = {m.pk: m.display_name for m in Medicine.objects.filter(pk__in=[r["medicine"] for r in top])}
        ctx["top_sellers"] = [{"name": names.get(r["medicine"], "—"), "value": r["value"], "units": r["units"]} for r in top]

        payments = {}
        for r in SalePayment.objects.filter(sale__in=month_sales).values("account__name").annotate(t=Sum("amount")):
            payments[r["account__name"]] = payments.get(r["account__name"], ZERO) + (r["t"] or ZERO)
        total_paid = sum(payments.values(), ZERO)
        ctx["payment_split"] = [{"name": n, "value": v, "share": float(v / total_paid * 100) if total_paid else 0}
                                for n, v in sorted(payments.items(), key=lambda kv: -kv[1])]

        recent = with_dues(sales).select_related("customer").order_by("-sold_at")[:6]
        ctx["recent_sales"] = [{"sale": s, "due": due_of(s)} for s in recent]
    if has_perm(user, "customer.list"):
        from apps.customers.models import Customer
        from apps.customers.services import with_balances

        owing = with_balances(Customer.objects.all()).filter(balance__gt=0).order_by("-balance")
        ctx["receivable"] = {"total": sum((c.balance for c in owing), ZERO), "count": owing.count(), "top": list(owing[:5])}
    if has_perm(user, "supplier.list"):
        from apps.suppliers.models import Supplier
        from apps.suppliers.services import with_balances as supplier_balances

        owed = supplier_balances(Supplier.objects.all()).filter(balance__gt=0).order_by("-balance")
        ctx["payable"] = {"total": sum((s.balance for s in owed), ZERO), "count": owed.count(), "top": list(owed[:5])}
    if has_perm(user, "stock.list"):
        from apps.stock.models import BatchStock

        value = BatchStock.objects.filter(outlet_id__in=outlet_ids, quantity__gt=0).aggregate(
            v=Sum(F("quantity") * F("batch__unit_cost"), output_field=VALUE))["v"]
        ctx["stock"] = {"value": Decimal(value or 0).quantize(Decimal("0.01"))}
    if ctx:
        from apps.setup.services import get_business_settings

        ctx["currency"] = get_business_settings(request.tenant).currency_symbol
    return ctx
