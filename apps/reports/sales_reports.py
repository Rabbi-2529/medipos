"""Sales and profit reports."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.customers.models import CustomerReceipt
from apps.items.models import Medicine
from apps.sales.models import Sale, SaleLine, SalePayment, SaleReturn, SaleReturnLine

from .engine import ZERO, Col, Link, Report, Result, pct, totals_for

MONEY = DecimalField(max_digits=16, decimal_places=2)


def sales_in(f):
    sales = Sale.objects.filter(outlet_id__in=f.outlet_ids, sold_at__date__gte=f.start, sold_at__date__lte=f.end)
    if f.customer:
        sales = sales.filter(customer=f.customer)
    if f.cashier:
        sales = sales.filter(created_by=f.cashier)
    return sales


def returns_in(f):
    returns = SaleReturn.objects.filter(outlet_id__in=f.outlet_ids, date__gte=f.start, date__lte=f.end)
    if f.customer:
        returns = returns.filter(customer=f.customer)
    return returns


def _sale_link(sale):
    return Link(sale.number, reverse("sales:detail", args=[sale.pk]))


def with_dues(sales):
    """Sales annotated with what was collected later and what returns took off the bill."""
    collected = CustomerReceipt.objects.filter(sale=OuterRef("pk")).values("sale").annotate(t=Sum("amount")).values("t")
    credited = SaleReturn.objects.filter(sale=OuterRef("pk")).values("sale").annotate(t=Sum("total") - Sum("refund_amount")).values("t")
    return sales.annotate(collected=Coalesce(Subquery(collected, output_field=MONEY), Value(ZERO), output_field=MONEY),
                          credited=Coalesce(Subquery(credited, output_field=MONEY), Value(ZERO), output_field=MONEY))


def due_of(sale):
    return max(ZERO, sale.total - sale.paid - sale.collected - sale.credited)


GROUPINGS = (
    ("medicine", gettext_lazy("Medicine")), ("category", gettext_lazy("Category")),
    ("manufacturer", gettext_lazy("Manufacturer")), ("generic", gettext_lazy("Generic")),
)
GROUP_PATH = {"category": "category__name", "manufacturer": "manufacturer__name", "generic": "generic__name"}


def group_name(medicine, group):
    if group in GROUP_PATH:
        related = getattr(medicine, group)
        return related.name if related else _("(none)")
    return medicine.display_name


class DailySales(Report):
    key, group, icon = "daily-sales", "sales", "bi-calendar3"
    title = gettext_lazy("Daily sales summary")
    description = gettext_lazy("Each day's invoices, discounts, VAT, money taken, dues given and returns.")

    def run(self, f):
        days = defaultdict(lambda: defaultdict(lambda: ZERO))
        for row in sales_in(f).annotate(day=TruncDate("sold_at")).values("day").annotate(
                n=Count("id"), gross=Sum("subtotal"), ld=Sum("line_discount"), cd=Sum("discount"), vat=Sum("vat_amount"),
                total=Sum("total"), paid=Sum("paid")):
            d = days[row["day"]]
            d["invoices"] = row["n"]
            d["gross"], d["discounts"], d["vat"] = row["gross"] or ZERO, (row["ld"] or ZERO) + (row["cd"] or ZERO), row["vat"] or ZERO
            d["total"], d["paid"] = row["total"] or ZERO, row["paid"] or ZERO
        for row in returns_in(f).values("date").annotate(t=Sum("total")):
            days[row["date"]]["returns"] = row["t"] or ZERO
        rows = []
        for day in sorted(days):
            d = days[day]
            rows.append({"date": day, "invoices": d["invoices"] or 0, "gross": d["gross"], "discounts": d["discounts"], "vat": d["vat"],
                         "total": d["total"], "paid": d["paid"], "due": d["total"] - d["paid"], "returns": d["returns"],
                         "net": d["total"] - d["returns"]})
        columns = [
            Col("date", _("Date"), "date"), Col("invoices", _("Invoices"), "int", True), Col("gross", _("Before discounts"), "money", True),
            Col("discounts", _("Discounts"), "money", True), Col("vat", _("VAT"), "money", True), Col("total", _("Sales"), "money", True),
            Col("paid", _("Paid at counter"), "money", True), Col("due", _("Given on due"), "money", True),
            Col("returns", _("Returns"), "money", True), Col("net", _("Net sales"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[
            (_("Net sales"), totals["net"], "money"), (_("Invoices"), totals["invoices"], "int"),
            (_("Average bill"), (totals["total"] / totals["invoices"]).quantize(Decimal("0.01")) if totals["invoices"] else ZERO, "money"),
            (_("Returns"), totals["returns"], "money"),
        ])


class SalesList(Report):
    key, group, icon = "sales", "sales", "bi-receipt"
    title = gettext_lazy("Sales")
    description = gettext_lazy("Every invoice with what was paid and what is still due.")
    filters = ("period", "outlet", "customer", "cashier", "option")
    option_label = gettext_lazy("Show")
    options = (("", gettext_lazy("All invoices")), ("due", gettext_lazy("Only with money due")))

    def run(self, f):
        rows = []
        for sale in with_dues(sales_in(f)).select_related("customer", "outlet", "created_by").order_by("sold_at"):
            due = due_of(sale)
            if f.option == "due" and not due:
                continue
            rows.append({
                "number": _sale_link(sale), "time": timezone.localtime(sale.sold_at).strftime("%d %b %Y, %I:%M %p"),
                "customer": sale.customer.name if sale.customer else _("Walk-in"), "cashier": str(sale.created_by or "—"),
                "outlet": sale.outlet.name, "total": sale.total, "paid": sale.paid + sale.collected, "due": due,
            })
        columns = [
            Col("number", _("Invoice")), Col("time", _("Time")), Col("customer", _("Customer")), Col("cashier", _("Cashier")),
            Col("outlet", _("Outlet")), Col("total", _("Total"), "money", True), Col("paid", _("Paid"), "money", True),
            Col("due", _("Due"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Invoices"), len(rows), "int"), (_("Sales"), totals["total"], "money"),
                                                    (_("Paid"), totals["paid"], "money"), (_("Due"), totals["due"], "money")])


class SaleItems(Report):
    key, group, icon = "sale-items", "sales", "bi-list-ul"
    title = gettext_lazy("Detailed sales")
    description = gettext_lazy("Every line sold: medicine, quantity, price and discount.")
    filters = ("period", "outlet", "customer", "medicine", "cashier")

    def run(self, f):
        lines = SaleLine.objects.filter(sale__in=sales_in(f)).select_related("sale", "sale__customer", "medicine").order_by("sale__sold_at", "id")
        if f.medicine:
            lines = lines.filter(medicine=f.medicine)
        rows = [{
            "date": timezone.localdate(line.sale.sold_at), "number": _sale_link(line.sale),
            "customer": line.sale.customer.name if line.sale.customer else _("Walk-in"), "medicine": line.medicine.display_name,
            "qty": f"{line.unit_qty} {line.unit_label}", "units": line.quantity, "price": line.unit_price.quantize(Decimal("0.01")),
            "discount": line.discount, "total": line.line_total,
        } for line in lines]
        columns = [
            Col("date", _("Date"), "date"), Col("number", _("Invoice")), Col("customer", _("Customer")), Col("medicine", _("Medicine")),
            Col("qty", _("Quantity")), Col("units", _("Units"), "int", True), Col("price", _("Price / unit"), "money"),
            Col("discount", _("Discount"), "money", True), Col("total", _("Total"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Lines"), len(rows), "int"), (_("Units"), totals["units"], "int"),
                                                    (_("Sales"), totals["total"], "money")])


class ProductSales(Report):
    key, group, icon = "product-sales", "sales", "bi-capsule"
    title = gettext_lazy("Product sales")
    description = gettext_lazy("What sold, by medicine, category, maker or generic: units, value and returns.")
    filters = ("period", "outlet", "category", "group")
    groupings = GROUPINGS

    def run(self, f):
        group = f.group if f.group in dict(GROUPINGS) else "medicine"
        lines = SaleLine.objects.filter(sale__in=sales_in(f)).select_related("medicine__category", "medicine__manufacturer", "medicine__generic")
        backs = SaleReturnLine.objects.filter(sale_return__in=returns_in(f)).select_related(
            "sale_line__medicine__category", "sale_line__medicine__manufacturer", "sale_line__medicine__generic")
        if f.category:
            lines = lines.filter(medicine__category__name=f.category)
            backs = backs.filter(sale_line__medicine__category__name=f.category)
        acc = defaultdict(lambda: {"invoices": set(), "units": 0, "value": ZERO, "ret_units": 0, "ret_value": ZERO})
        for line in lines:
            row = acc[group_name(line.medicine, group)]
            row["invoices"].add(line.sale_id)
            row["units"] += line.quantity
            row["value"] += line.line_total
        for back in backs:
            row = acc[group_name(back.sale_line.medicine, group)]
            row["ret_units"] += back.quantity
            row["ret_value"] += back.line_total
        total_net = sum((r["value"] - r["ret_value"] for r in acc.values()), ZERO)
        rows = sorted(({"name": name, "invoices": len(r["invoices"]), "units": r["units"], "ret_units": r["ret_units"],
                        "net_units": r["units"] - r["ret_units"], "value": r["value"], "ret_value": r["ret_value"],
                        "net": r["value"] - r["ret_value"], "share": pct(r["value"] - r["ret_value"], total_net)}
                       for name, r in acc.items()), key=lambda r: -r["net"])
        columns = [
            Col("name", dict(GROUPINGS)[group]), Col("invoices", _("Invoices"), "int"), Col("units", _("Units sold"), "int", True),
            Col("ret_units", _("Units returned"), "int", True), Col("net_units", _("Net units"), "int", True),
            Col("value", _("Sales value"), "money", True), Col("ret_value", _("Returns"), "money", True),
            Col("net", _("Net sales"), "money", True), Col("share", _("Share"), "pct"),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Items"), len(rows), "int"), (_("Net units"), totals["net_units"], "int"),
                                                    (_("Net sales"), totals["net"], "money")])


class DueSales(Report):
    key, group, icon = "due-sales", "sales", "bi-hourglass-split"
    title = gettext_lazy("Due sales")
    description = gettext_lazy("Invoices customers haven't fully paid yet, oldest first.")
    filters = ("period", "outlet", "customer")
    default_days = 365

    def run(self, f):
        today = timezone.localdate()
        rows = []
        for sale in with_dues(sales_in(f).filter(customer__isnull=False)).select_related("customer").order_by("sold_at"):
            due = due_of(sale)
            if not due:
                continue
            sold = timezone.localdate(sale.sold_at)
            rows.append({
                "number": _sale_link(sale), "date": sold, "customer": Link(sale.customer.name, reverse("customers:detail", args=[sale.customer_id])),
                "phone": sale.customer.phone, "total": sale.total, "paid": sale.paid + sale.collected, "credited": sale.credited,
                "due": due, "age": (today - sold).days,
            })
        columns = [
            Col("number", _("Invoice")), Col("date", _("Date"), "date"), Col("customer", _("Customer")), Col("phone", _("Phone")),
            Col("total", _("Total"), "money", True), Col("paid", _("Paid"), "money", True), Col("credited", _("Returned"), "money", True),
            Col("due", _("Due"), "money", True), Col("age", _("Days"), "int"),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Invoices with due"), len(rows), "int"), (_("Due"), totals["due"], "money"),
                                                    (_("Customers"), len({r["customer"].text for r in rows}), "int")])


class PaymentMethods(Report):
    key, group, icon = "payments", "sales", "bi-wallet2"
    title = gettext_lazy("Sales by payment method")
    description = gettext_lazy("Money taken per account (cash, bKash, bank…): at the counter, due collected, refunds.")

    def run(self, f):
        acc = defaultdict(lambda: {"sales": ZERO, "collections": ZERO, "refunds": ZERO})
        for row in SalePayment.objects.filter(sale__in=sales_in(f)).values("account__name").annotate(t=Sum("amount")):
            acc[row["account__name"]]["sales"] += row["t"] or ZERO
        receipts = CustomerReceipt.objects.filter(date__gte=f.start, date__lte=f.end).filter(Q(outlet_id__in=f.outlet_ids) | Q(outlet__isnull=True))
        for row in receipts.values("account__name").annotate(t=Sum("amount")):
            acc[row["account__name"]]["collections"] += row["t"] or ZERO
        for row in returns_in(f).exclude(refund_account=None).values("refund_account__name").annotate(t=Sum("refund_amount")):
            acc[row["refund_account__name"]]["refunds"] += row["t"] or ZERO
        total_net = sum((r["sales"] + r["collections"] - r["refunds"] for r in acc.values()), ZERO)
        rows = sorted(({"account": name, **r, "net": r["sales"] + r["collections"] - r["refunds"],
                        "share": pct(r["sales"] + r["collections"] - r["refunds"], total_net)} for name, r in acc.items()),
                      key=lambda r: -r["net"])
        columns = [
            Col("account", _("Account")), Col("sales", _("At the counter"), "money", True), Col("collections", _("Due collected"), "money", True),
            Col("refunds", _("Refunds"), "money", True), Col("net", _("Net"), "money", True), Col("share", _("Share"), "pct"),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Net taken"), totals["net"], "money"), (_("Refunds"), totals["refunds"], "money")])


# ---- Profit -----------------------------------------------------------------------------

def _revenue_share(sale):
    """Part of each line's total that is income: after the cart discount, without VAT (rounding included)."""
    base = sale.subtotal - sale.line_discount
    return (sale.total - sale.vat_amount) / base if base else Decimal("0")


class ProductProfit(Report):
    key, group, icon = "product-profit", "profit", "bi-graph-up-arrow"
    title = gettext_lazy("Product profit")
    description = gettext_lazy("Income, cost and profit per medicine, category or maker, returns taken off.")
    permission = "profit_report.list"
    filters = ("period", "outlet", "category", "group")
    groupings = GROUPINGS

    def run(self, f):
        group = f.group if f.group in dict(GROUPINGS) else "medicine"
        acc = defaultdict(lambda: {"units": 0, "revenue": ZERO, "cost": ZERO})
        lines = SaleLine.objects.filter(sale__in=sales_in(f)).select_related("sale", "medicine__category", "medicine__manufacturer", "medicine__generic")
        backs = SaleReturnLine.objects.filter(sale_return__in=returns_in(f)).select_related(
            "sale_line__sale", "sale_line__medicine__category", "sale_line__medicine__manufacturer", "sale_line__medicine__generic")
        if f.category:
            lines = lines.filter(medicine__category__name=f.category)
            backs = backs.filter(sale_line__medicine__category__name=f.category)
        for line in lines:
            row = acc[group_name(line.medicine, group)]
            row["units"] += line.quantity
            row["revenue"] += line.line_total * _revenue_share(line.sale)
            row["cost"] += line.cost_total
        for back in backs:
            line, sale = back.sale_line, back.sale_line.sale
            row = acc[group_name(line.medicine, group)]
            row["units"] -= back.quantity
            row["revenue"] -= back.line_total * ((sale.total - sale.vat_amount) / sale.total if sale.total else 0)
            row["cost"] -= line.cost_total / line.quantity * back.quantity if line.quantity else 0
        rows = []
        for name, r in acc.items():
            revenue, cost = r["revenue"].quantize(Decimal("0.01")), Decimal(r["cost"]).quantize(Decimal("0.01"))
            rows.append({"name": name, "units": r["units"], "revenue": revenue, "cost": cost, "profit": revenue - cost, "margin": pct(revenue - cost, revenue)})
        rows.sort(key=lambda r: -r["profit"])
        columns = [
            Col("name", dict(GROUPINGS)[group]), Col("units", _("Net units"), "int", True), Col("revenue", _("Income"), "money", True),
            Col("cost", _("Cost"), "money", True), Col("profit", _("Profit"), "money", True), Col("margin", _("Margin"), "pct"),
        ]
        totals = totals_for(columns, rows)
        totals["margin"] = pct(totals["profit"], totals["revenue"])
        return Result(columns, rows, totals, tiles=[(_("Income"), totals["revenue"], "money"), (_("Cost"), totals["cost"], "money"),
                                                    (_("Profit"), totals["profit"], "money"), (_("Margin"), totals["margin"], "pct")],
                      note=_("Income is after discounts and without VAT. Expenses are not included; see Profit & loss for those."))


class InvoiceProfit(Report):
    key, group, icon = "invoice-profit", "profit", "bi-receipt-cutoff"
    title = gettext_lazy("Profit by invoice")
    description = gettext_lazy("Income, cost and profit of every sale.")
    permission = "profit_report.list"
    filters = ("period", "outlet", "customer", "cashier")

    def run(self, f):
        rows = []
        for sale in sales_in(f).select_related("customer").order_by("sold_at"):
            revenue, cost = sale.total - sale.vat_amount, Decimal(sale.cost_total).quantize(Decimal("0.01"))
            rows.append({"number": _sale_link(sale), "date": timezone.localdate(sale.sold_at),
                         "customer": sale.customer.name if sale.customer else _("Walk-in"),
                         "revenue": revenue, "cost": cost, "profit": revenue - cost, "margin": pct(revenue - cost, revenue)})
        columns = [
            Col("number", _("Invoice")), Col("date", _("Date"), "date"), Col("customer", _("Customer")),
            Col("revenue", _("Income"), "money", True), Col("cost", _("Cost"), "money", True), Col("profit", _("Profit"), "money", True),
            Col("margin", _("Margin"), "pct"),
        ]
        totals = totals_for(columns, rows)
        totals["margin"] = pct(totals["profit"], totals["revenue"])
        return Result(columns, rows, totals, tiles=[(_("Invoices"), len(rows), "int"), (_("Profit"), totals["profit"], "money"),
                                                    (_("Margin"), totals["margin"], "pct")],
                      note=_("Returns are not taken off here; Product profit does that."))


class PriceHistory(Report):
    key, group, icon = "price-history", "profit", "bi-clock-history"
    title = gettext_lazy("Price history")
    description = gettext_lazy("What a medicine cost from each supplier over time, and the MRP set with each purchase.")
    permission = "profit_report.list"
    filters = ("period", "outlet", "medicine")
    needs = "medicine"
    default_days = 365

    def run(self, f):
        from apps.purchases.models import Purchase, PurchaseLine

        lines = (PurchaseLine.objects.filter(medicine=f.medicine, purchase__status=Purchase.Status.RECEIVED, purchase__outlet_id__in=f.outlet_ids,
                                             purchase__date__gte=f.start, purchase__date__lte=f.end)
                 .select_related("purchase", "purchase__supplier").order_by("purchase__date", "id"))
        rows = [{
            "date": line.purchase.date, "number": Link(line.purchase.number, reverse("purchases:detail", args=[line.purchase_id])),
            "supplier": line.purchase.supplier.name, "batch": line.batch_no or "—", "units": line.total_units,
            "cost": line.unit_cost.quantize(Decimal("0.0001")), "mrp": line.sale_price,
            "margin": pct(line.sale_price - line.unit_cost, line.sale_price) if line.sale_price else None,
        } for line in lines]
        columns = [
            Col("date", _("Date"), "date"), Col("number", _("Purchase")), Col("supplier", _("Supplier")), Col("batch", _("Batch")),
            Col("units", _("Units"), "int", True), Col("cost", _("Cost / unit"), "money"), Col("mrp", _("MRP set"), "money"),
            Col("margin", _("Margin at MRP"), "pct"),
        ]
        medicine = Medicine.objects.get(pk=f.medicine.pk)
        costs = [r["cost"] for r in rows]
        return Result(columns, rows, totals_for(columns, rows), tiles=[
            (_("MRP now"), medicine.sale_price, "money"), (_("Purchase price now"), medicine.purchase_price, "money"),
            (_("Lowest cost"), min(costs) if costs else ZERO, "money"), (_("Highest cost"), max(costs) if costs else ZERO, "money"),
        ])
