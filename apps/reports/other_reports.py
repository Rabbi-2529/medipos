"""Stock and purchase, customer and supplier ledger, and staff reports."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.customers.models import Customer
from apps.customers.services import with_balances as customer_balances
from apps.purchases.models import Purchase, PurchaseLine, PurchaseReturn
from apps.sales.models import CashRegister, Sale, SaleLine, SaleReturn
from apps.stock.models import BatchStock, StockMovement
from apps.stock.services import format_quantity
from apps.suppliers.models import Supplier, SupplierPayment
from apps.suppliers.services import with_balances as supplier_balances

from .engine import ZERO, Col, Link, Report, Result, pct, totals_for
from .sales_reports import sales_in

MONEY = DecimalField(max_digits=16, decimal_places=2)
VALUE = DecimalField(max_digits=18, decimal_places=4)


def purchases_in(f):
    purchases = Purchase.objects.filter(status=Purchase.Status.RECEIVED, outlet_id__in=f.outlet_ids, date__gte=f.start, date__lte=f.end)
    return purchases.filter(supplier=f.supplier) if f.supplier else purchases


class Purchases(Report):
    key, group, icon = "purchases", "stock", "bi-bag-check"
    title = gettext_lazy("Purchases")
    description = gettext_lazy("Received purchases with what was paid, returned and is still owed.")
    filters = ("period", "outlet", "supplier")

    def run(self, f):
        paid = SupplierPayment.objects.filter(purchase=OuterRef("pk")).values("purchase").annotate(t=Sum("amount")).values("t")
        returned = PurchaseReturn.objects.filter(purchase=OuterRef("pk")).values("purchase").annotate(t=Sum("total")).values("t")
        purchases = purchases_in(f).select_related("supplier", "outlet").annotate(
            paid_sum=Coalesce(Subquery(paid, output_field=MONEY), Value(ZERO), output_field=MONEY),
            returned_sum=Coalesce(Subquery(returned, output_field=MONEY), Value(ZERO), output_field=MONEY)).order_by("date", "id")
        rows = [{
            "number": Link(p.number, reverse("purchases:detail", args=[p.pk])), "date": p.date, "supplier": p.supplier.name,
            "invoice": p.supplier_invoice_no or "—", "outlet": p.outlet.name, "total": p.total, "paid": p.paid_sum,
            "returned": p.returned_sum, "due": p.total - p.paid_sum - p.returned_sum,
        } for p in purchases]
        columns = [
            Col("number", _("Purchase")), Col("date", _("Date"), "date"), Col("supplier", _("Supplier")), Col("invoice", _("Supplier invoice")),
            Col("outlet", _("Outlet")), Col("total", _("Total"), "money", True), Col("paid", _("Paid"), "money", True),
            Col("returned", _("Returned"), "money", True), Col("due", _("Due"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Purchases"), len(rows), "int"), (_("Bought"), totals["total"], "money"),
                                                    (_("Still owed"), totals["due"], "money")])


class PurchaseItems(Report):
    key, group, icon = "purchase-items", "stock", "bi-boxes"
    title = gettext_lazy("Product purchases")
    description = gettext_lazy("Units and cost bought per medicine, free units and the last supplier.")
    filters = ("period", "outlet", "supplier", "category")

    def run(self, f):
        lines = PurchaseLine.objects.filter(purchase__in=purchases_in(f)).select_related("medicine", "purchase__supplier").order_by("purchase__date", "id")
        if f.category:
            lines = lines.filter(medicine__category__name=f.category)
        acc = defaultdict(lambda: {"purchases": set(), "units": 0, "free": 0, "cost": ZERO, "supplier": ""})
        for line in lines:
            row = acc[line.medicine.display_name]
            row["purchases"].add(line.purchase_id)
            row["units"] += line.total_units
            row["free"] += line.free_units
            row["cost"] += line.line_total
            row["supplier"] = line.purchase.supplier.name
        rows = sorted(({"name": name, "purchases": len(r["purchases"]), "units": r["units"], "free": r["free"], "cost": r["cost"],
                        "avg": (r["cost"] / r["units"]).quantize(Decimal("0.0001")) if r["units"] else ZERO, "supplier": r["supplier"]}
                       for name, r in acc.items()), key=lambda r: -r["cost"])
        columns = [
            Col("name", _("Medicine")), Col("purchases", _("Purchases"), "int"), Col("units", _("Units in"), "int", True),
            Col("free", _("Free units"), "int", True), Col("cost", _("Cost"), "money", True), Col("avg", _("Cost / unit"), "money"),
            Col("supplier", _("Last supplier")),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Medicines"), len(rows), "int"), (_("Units in"), totals["units"], "int"),
                                                    (_("Cost"), totals["cost"], "money")])


class StockValue(Report):
    key, group, icon = "stock-value", "stock", "bi-safe"
    title = gettext_lazy("Stock value by category")
    description = gettext_lazy("What the stock on the shelves is worth at cost and at MRP, by category.")
    filters = ("outlet", "group")
    groupings = (("category", gettext_lazy("Category")), ("manufacturer", gettext_lazy("Manufacturer")), ("outlet", gettext_lazy("Outlet")))

    def run(self, f):
        group = f.group if f.group in dict(self.groupings) else "category"
        path = {"category": "medicine__category__name", "manufacturer": "medicine__manufacturer__name", "outlet": "outlet__name"}[group]
        stock = BatchStock.objects.filter(outlet_id__in=f.outlet_ids, quantity__gt=0)
        today = timezone.localdate()
        rows = []
        for row in stock.values(path).annotate(
                medicines=Count("medicine", distinct=True), units=Sum("quantity"),
                cost=Sum(F("quantity") * F("batch__unit_cost"), output_field=VALUE), mrp=Sum(F("quantity") * F("medicine__sale_price"), output_field=VALUE)):
            cost, mrp = Decimal(row["cost"] or 0).quantize(Decimal("0.01")), Decimal(row["mrp"] or 0).quantize(Decimal("0.01"))
            rows.append({"name": row[path] or _("(none)"), "medicines": row["medicines"], "units": row["units"], "cost": cost, "mrp": mrp,
                         "margin": mrp - cost, "margin_pct": pct(mrp - cost, mrp)})
        rows.sort(key=lambda r: -r["cost"])
        expired = stock.filter(batch__expiry_date__lte=today).aggregate(v=Sum(F("quantity") * F("batch__unit_cost"), output_field=VALUE))["v"] or 0
        columns = [
            Col("name", dict(self.groupings)[group]), Col("medicines", _("Medicines"), "int", True), Col("units", _("Units"), "int", True),
            Col("cost", _("Value at cost"), "money", True), Col("mrp", _("Value at MRP"), "money", True),
            Col("margin", _("Margin in stock"), "money", True), Col("margin_pct", _("Margin"), "pct"),
        ]
        totals = totals_for(columns, rows)
        totals["margin_pct"] = pct(totals["margin"], totals["mrp"])
        return Result(columns, rows, totals, tiles=[(_("Value at cost"), totals["cost"], "money"), (_("Value at MRP"), totals["mrp"], "money"),
                                                    (_("Expired, at cost"), Decimal(expired).quantize(Decimal("0.01")), "money")])


class ItemTracking(Report):
    key, group, icon = "stock-movements", "stock", "bi-arrow-left-right"
    title = gettext_lazy("Stock movements")
    description = gettext_lazy("Every unit in and out: purchases, sales, returns, transfers, write-offs and counts.")
    filters = ("period", "outlet", "medicine", "option")
    option_label = gettext_lazy("Movement")
    options = (("", gettext_lazy("All movements")), *StockMovement.Kind.choices)

    def run(self, f):
        moves = (StockMovement.objects.filter(outlet_id__in=f.outlet_ids, created_at__date__gte=f.start, created_at__date__lte=f.end)
                 .select_related("medicine", "batch", "outlet", "created_by", "medicine__sale_unit").order_by("created_at", "id"))
        if f.medicine:
            moves = moves.filter(medicine=f.medicine)
        if f.option in StockMovement.Kind.values:
            moves = moves.filter(kind=f.option)
        rows = [{
            "time": timezone.localtime(m.created_at).strftime("%d %b %Y, %I:%M %p"), "medicine": m.medicine.display_name, "batch": str(m.batch),
            "outlet": m.outlet.name, "kind": m.get_kind_display(), "ref": m.ref_label or "—",
            "in": m.quantity if m.quantity > 0 else 0, "out": -m.quantity if m.quantity < 0 else 0, "balance": m.balance_after,
            "user": str(m.created_by or "—"),
        } for m in moves]
        columns = [
            Col("time", _("Time")), Col("medicine", _("Medicine")), Col("batch", _("Batch")), Col("outlet", _("Outlet")),
            Col("kind", _("Movement")), Col("ref", _("Document")), Col("in", _("In"), "int", True), Col("out", _("Out"), "int", True),
            Col("balance", _("Batch balance"), "int"), Col("user", _("By")),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Movements"), len(rows), "int"), (_("Units in"), totals["in"], "int"),
                                                    (_("Units out"), totals["out"], "int")])


# ---- Customers & suppliers ----------------------------------------------------------------

class CustomerBalances(Report):
    key, group, icon = "customer-balances", "ledgers", "bi-person-lines-fill"
    title = gettext_lazy("Customer balances")
    description = gettext_lazy("What each customer has bought, how much they owe, and their credit limit.")
    filters = ("option",)
    option_label = gettext_lazy("Show")
    options = (("due", gettext_lazy("Only customers who owe")), ("all", gettext_lazy("All customers")))

    def run(self, f):
        customers = customer_balances(Customer.objects.all()).annotate(bought=Coalesce(Sum("sales__total"), Value(ZERO), output_field=MONEY),
                                                                       last=Max("sales__sold_at")).order_by("-balance", "name")
        if f.option != "all":
            customers = customers.filter(balance__gt=0)
        rows = [{
            "name": Link(c.name, reverse("customers:detail", args=[c.pk])), "phone": c.phone or "—", "bought": c.bought,
            "balance": c.balance, "limit": c.credit_limit, "last": timezone.localdate(c.last) if c.last else None,
        } for c in customers]
        columns = [
            Col("name", _("Customer")), Col("phone", _("Phone")), Col("bought", _("Bought (all time)"), "money", True),
            Col("balance", _("Owes"), "money", True), Col("limit", _("Credit limit"), "money"), Col("last", _("Last sale"), "date"),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Customers"), len(rows), "int"), (_("Total owed to you"), totals["balance"], "money")])


class SupplierBalances(Report):
    key, group, icon = "supplier-balances", "ledgers", "bi-truck"
    title = gettext_lazy("Supplier balances")
    description = gettext_lazy("What you bought from each supplier and what you still owe them.")
    filters = ("option",)
    option_label = gettext_lazy("Show")
    options = (("due", gettext_lazy("Only suppliers you owe")), ("all", gettext_lazy("All suppliers")))

    def run(self, f):
        bought = (Purchase.objects.filter(supplier=OuterRef("pk"), status=Purchase.Status.RECEIVED).values("supplier")
                  .annotate(t=Sum("total")).values("t"))
        suppliers = supplier_balances(Supplier.objects.all()).annotate(
            bought=Coalesce(Subquery(bought, output_field=MONEY), Value(ZERO), output_field=MONEY)).order_by("-balance", "name")
        if f.option != "all":
            suppliers = suppliers.filter(balance__gt=0)
        rows = [{"name": Link(s.name, reverse("suppliers:detail", args=[s.pk])), "phone": s.phone or "—", "bought": s.bought, "balance": s.balance}
                for s in suppliers]
        columns = [Col("name", _("Supplier")), Col("phone", _("Phone")), Col("bought", _("Bought (all time)"), "money", True),
                   Col("balance", _("You owe"), "money", True)]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Suppliers"), len(rows), "int"), (_("Total you owe"), totals["balance"], "money")])


def _ledger(entries, start, sign):
    """Opening balance before `start`, then each entry with a running balance (sign 1: debit grows it; -1: credit does)."""
    before = entries.filter(date__lt=start).aggregate(d=Sum("debit"), c=Sum("credit"))
    opening = sign * ((before["d"] or ZERO) - (before["c"] or ZERO))
    running, rows = opening, []
    for e in entries.filter(date__gte=start).order_by("date", "id"):
        running += sign * (e.debit - e.credit)
        rows.append({"date": e.date, "kind": e.get_kind_display(), "ref": e.ref_label or "—", "note": e.note or "",
                     "debit": e.debit, "credit": e.credit, "balance": running})
    return opening, rows


class CustomerLedger(Report):
    key, group, icon = "customer-ledger", "ledgers", "bi-journal-text"
    title = gettext_lazy("Customer ledger")
    description = gettext_lazy("One customer's sales, payments and returns with a running balance.")
    filters = ("period", "customer")
    needs = "customer"
    default_days = 365

    def run(self, f):
        opening, rows = _ledger(f.customer.ledger.filter(date__lte=f.end), f.start, 1)
        columns = [Col("date", _("Date"), "date"), Col("kind", _("Entry")), Col("ref", _("Document")), Col("note", _("Note")),
                   Col("debit", _("Bought / refunded"), "money", True), Col("credit", _("Paid / returned"), "money", True),
                   Col("balance", _("Owes"), "money")]
        closing = rows[-1]["balance"] if rows else opening
        return Result(columns, rows, totals_for(columns, rows), tiles=[(_("Owed at the start"), opening, "money"), (_("Owes now"), closing, "money")])


class SupplierLedger(Report):
    key, group, icon = "supplier-ledger", "ledgers", "bi-journal-richtext"
    title = gettext_lazy("Supplier ledger")
    description = gettext_lazy("One supplier's purchases, payments and returns with a running balance.")
    filters = ("period", "supplier")
    needs = "supplier"
    default_days = 365

    def run(self, f):
        opening, rows = _ledger(f.supplier.ledger.filter(date__lte=f.end), f.start, -1)
        columns = [Col("date", _("Date"), "date"), Col("kind", _("Entry")), Col("ref", _("Document")), Col("note", _("Note")),
                   Col("credit", _("Bought"), "money", True), Col("debit", _("Paid / returned"), "money", True),
                   Col("balance", _("You owe"), "money")]
        closing = rows[-1]["balance"] if rows else opening
        return Result(columns, rows, totals_for(columns, rows), tiles=[(_("Owed at the start"), opening, "money"), (_("You owe now"), closing, "money")])


# ---- Staff ------------------------------------------------------------------------------

class EmployeeSales(Report):
    key, group, icon = "employee-sales", "staff", "bi-person-badge"
    title = gettext_lazy("Sales by employee")
    description = gettext_lazy("Invoices, sales, average bill, discounts given and returns taken by each cashier.")

    def run(self, f):
        sales = sales_in(f)
        units = {r["sale__created_by"]: r["u"] for r in SaleLine.objects.filter(sale__in=sales).values("sale__created_by").annotate(u=Sum("quantity"))}
        returns = {r["created_by"]: r["t"] for r in SaleReturn.objects.filter(outlet_id__in=f.outlet_ids, date__gte=f.start, date__lte=f.end)
                   .values("created_by").annotate(t=Sum("total"))}
        rows = []
        for r in sales.values("created_by", "created_by__first_name", "created_by__last_name", "created_by__email").annotate(
                n=Count("id"), total=Sum("total"), ld=Sum("line_discount"), cd=Sum("discount")).order_by("-total"):
            name = " ".join(filter(None, [r["created_by__first_name"], r["created_by__last_name"]])) or r["created_by__email"] or "—"
            total = r["total"] or ZERO
            rows.append({"name": name, "invoices": r["n"], "units": units.get(r["created_by"], 0), "total": total,
                         "avg": (total / r["n"]).quantize(Decimal("0.01")) if r["n"] else ZERO,
                         "discounts": (r["ld"] or ZERO) + (r["cd"] or ZERO), "returns": returns.get(r["created_by"]) or ZERO})
        columns = [
            Col("name", _("Employee")), Col("invoices", _("Invoices"), "int", True), Col("units", _("Units sold"), "int", True),
            Col("total", _("Sales"), "money", True), Col("avg", _("Average bill"), "money"), Col("discounts", _("Discounts given"), "money", True),
            Col("returns", _("Returns taken"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Sales"), totals["total"], "money"), (_("Invoices"), totals["invoices"], "int")])


class Registers(Report):
    key, group, icon = "registers", "staff", "bi-cash-stack"
    title = gettext_lazy("Cash registers")
    description = gettext_lazy("Every till session with expected and counted cash, and the difference.")
    filters = ("period", "outlet", "cashier")

    def run(self, f):
        from apps.sales.services import register_summary

        registers = (CashRegister.objects.filter(outlet_id__in=f.outlet_ids, opened_at__date__gte=f.start, opened_at__date__lte=f.end)
                     .select_related("outlet", "opened_by").order_by("opened_at"))
        if f.cashier:
            registers = registers.filter(opened_by=f.cashier)
        rows = []
        for r in registers:
            expected = r.expected_cash if r.closed_at else register_summary(r)["expected_cash"]
            rows.append({
                "opened": Link(timezone.localtime(r.opened_at).strftime("%d %b %Y, %I:%M %p"), reverse("sales:register", args=[r.pk])),
                "closed": timezone.localtime(r.closed_at).strftime("%d %b, %I:%M %p") if r.closed_at else _("Still open"),
                "cashier": str(r.opened_by), "outlet": r.outlet.name, "opening": r.opening_cash, "expected": expected,
                "counted": r.counted_cash, "difference": (r.counted_cash - expected) if r.counted_cash is not None else None,
            })
        columns = [
            Col("opened", _("Opened")), Col("closed", _("Closed")), Col("cashier", _("Cashier")), Col("outlet", _("Outlet")),
            Col("opening", _("Opening cash"), "money"), Col("expected", _("Expected"), "money"), Col("counted", _("Counted"), "money"),
            Col("difference", _("Short / over"), "money", True),
        ]
        totals = totals_for(columns, [r for r in rows if r["difference"] is not None])
        return Result(columns, rows, totals, tiles=[(_("Sessions"), len(rows), "int"), (_("Short / over in total"), totals["difference"], "money")],
                      note=_("A negative difference means cash was short; positive means there was more than expected."))


class StaffAttendance(Report):
    key, group, icon = "staff-attendance", "staff", "bi-calendar-check"
    title = gettext_lazy("Staff attendance")
    description = gettext_lazy("Days present, late, on half day, absent or on leave for each person, and the days that count against pay.")
    permission = "attendance.list"
    filters = ("period",)

    def run(self, f):
        from apps.hr.models import Attendance
        from apps.hr.services import absent_days, active_staff, attendance_counts

        S = Attendance.Status
        marked = Attendance.objects.filter(date__gte=f.start, date__lte=f.end)
        people = {e.pk: e for e in active_staff()}
        people.update({a.employee_id: a.employee for a in marked.select_related("employee").distinct() if a.employee_id not in people})
        counts = attendance_counts(list(people), f.start, f.end)
        away = {r["employee"]: r["n"] for r in marked.exclude(in_reason="", out_reason="").values("employee").annotate(n=Count("id"))}
        early = {r["employee"]: r["n"] for r in marked.filter(left_early=True).values("employee").annotate(n=Count("id"))}
        days = (min(f.end, timezone.localdate()) - f.start).days + 1
        rows = []
        for employee in sorted(people.values(), key=lambda e: e.name):
            c = counts.get(employee.pk, {})
            rows.append({
                "name": Link(employee.display_name, f"{reverse('hr:attendance_calendar')}?employee={employee.pk}"),
                "designation": employee.designation, **{status: c.get(status, 0) for status in S.values},
                "unmarked": max(days - sum(c.values()), 0), "off_pay": absent_days(c), "early": early.get(employee.pk, 0),
                "away": away.get(employee.pk, 0),
            })
        columns = [Col("name", _("Employee")), Col("designation", _("Designation"))]
        columns += [Col(value, label, "int", True) for value, label in S.choices]
        columns += [Col("unmarked", _("Not marked"), "int", True), Col("off_pay", _("Days off pay"), "int", True),
                    Col("early", _("Left early"), "int", True), Col("away", _("Checked in/out off the shop network"), "int", True)]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Person-days present"), totals[S.PRESENT] + totals[S.LATE], "int"),
                                                     (_("Absent days"), totals[S.ABSENT], "int")],
                      note=_("Days off pay = absent days plus half of the half days. Late still counts as present."))


class SalariesPaid(Report):
    key, group, icon = "salaries-paid", "staff", "bi-cash-coin"
    title = gettext_lazy("Salaries paid")
    description = gettext_lazy("Each person's pay from the payrolls paid in the period: earnings, deductions, advances taken back and take-home.")
    permission = "salary.list"
    filters = ("period",)
    default_days = 365

    def run(self, f):
        from apps.hr.models import Payroll, PayrollLine

        lines = (PayrollLine.objects.filter(payroll__status=Payroll.Status.PAID, payroll__paid_on__gte=f.start, payroll__paid_on__lte=f.end)
                 .select_related("payroll", "employee").order_by("payroll__month", "employee__name"))
        rows = [{
            "month": Link(line.payroll.month.strftime("%b %Y"), reverse("hr:payroll_detail", args=[line.payroll_id])),
            "name": line.employee.display_name, "earnings": line.earnings, "deductions": line.absence_deduction + line.other_deduction,
            "gross": line.gross, "advance": line.advance_deduction, "net": line.net,
        } for line in lines]
        columns = [
            Col("month", _("Month")), Col("name", _("Employee")), Col("earnings", _("Earnings"), "money", True),
            Col("deductions", _("Absence & other deductions"), "money", True), Col("gross", _("Salary cost"), "money", True),
            Col("advance", _("Advance taken back"), "money", True), Col("net", _("Take-home"), "money", True),
        ]
        totals = totals_for(columns, rows)
        return Result(columns, rows, totals, tiles=[(_("Salary cost"), totals["gross"], "money"), (_("Paid out"), totals["net"], "money")])
