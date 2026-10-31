from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy, make_supplier
from apps.customers.models import Customer
from apps.customers.services import collect
from apps.outlets.models import Outlet
from apps.purchases.services import clean_lines, receive, save_draft
from apps.sales.services import complete_sale, create_return
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from .views import REPORTS

TODAY = timezone.localdate()


class ReportTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=10, sale_price="2.00")
        self.supplier = make_supplier(self.tenant)
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
            self.cash = PaymentAccount.objects.get(name="Cash")
            batch = get_or_create_batch(self.napa, "B1", TODAY + timedelta(days=300), Decimal("1.50"))
            post_movement(outlet=self.outlet, batch=batch, quantity=200, kind=StockMovement.Kind.OPENING)
            self.rahim = Customer.objects.create(name="Rahim", phone="017")
            sell = lambda qty, paid, **kw: complete_sale(tenant=self.tenant, outlet=self.outlet, user=self.owner,  # noqa: E731
                                                         lines=[{"medicine": self.napa.pk, "qty": qty}],
                                                         payments=[{"account": self.cash.pk, "amount": paid}] if paid else [], **kw)
            sell(10, 20)
            self.due_sale = sell(30, 20, customer=self.rahim)  # 60 total, 40 due
            create_return(self.due_sale, {self.due_sale.lines.get().pk: 5}, date=TODAY)  # 10 back, credited
            collect(self.rahim, amount=Decimal("5"), account=self.cash, date=TODAY, sale=self.due_sale)
            purchase = save_draft(tenant=self.tenant, outlet=self.outlet, supplier=self.supplier, date=TODAY, user=self.owner,
                                  lines=clean_lines([{"medicine": self.napa.pk, "batch_no": "P1", "expiry": (TODAY + timedelta(days=400)).strftime("%Y-%m"),
                                                      "packs": 2, "units": 0, "free_units": 0, "pack_cost": "15", "sale_price": "2"}]))
            receive(purchase, user=self.owner)
        self.client.force_login(self.owner)

    def report(self, key, **params):
        response = self.client.get(reverse("reports:report", args=[key]), params)
        self.assertEqual(response.status_code, 200, key)
        return response.context["result"]

    def test_every_report_opens_and_exports(self):
        self.assertContains(self.client.get(reverse("reports:index")), "Product profit")
        needs = {"customer": self.rahim.pk, "supplier": self.supplier.pk, "medicine": self.napa.pk}
        for report in REPORTS:
            params = {report.needs: needs[report.needs]} if report.needs else {}
            result = self.report(report.key, **params)
            export = self.client.get(reverse("reports:report", args=[report.key]), {**params, "export": "xlsx"})
            sheet = load_workbook(BytesIO(export.content)).active
            self.assertEqual(sheet.cell(row=1, column=1).value, str(result.columns[0].label), report.key)
        waiting = self.client.get(reverse("reports:report", args=["customer-ledger"]))
        self.assertContains(waiting, "Choose a customer")

    def test_sales_figures(self):
        daily = self.report("daily-sales")
        self.assertEqual((daily.totals["invoices"], daily.totals["total"], daily.totals["returns"], daily.totals["net"]),
                         (2, Decimal("80"), Decimal("10"), Decimal("70")))
        due = self.report("due-sales")
        self.assertEqual([row["due"] for row in due.rows], [Decimal("25")])  # 60 - 20 paid - 5 collected - 10 returned
        product = self.report("product-sales")
        self.assertEqual((product.rows[0]["units"], product.rows[0]["ret_units"], product.rows[0]["net"]), (40, 5, Decimal("70")))
        only_due = self.report("sales", option="due")
        self.assertEqual(len(only_due.rows), 1)

    def test_profit_and_ledgers(self):
        profit = self.report("product-profit")
        self.assertEqual((profit.totals["revenue"], profit.totals["cost"], profit.totals["profit"]), (Decimal("70.00"), Decimal("52.50"), Decimal("17.50")))
        ledger = self.report("customer-ledger", customer=self.rahim.pk)
        self.assertEqual(ledger.rows[-1]["balance"], Decimal("25"))
        suppliers = self.report("supplier-balances")
        self.assertEqual(suppliers.totals["balance"], Decimal("30"))
        prices = self.report("price-history", medicine=self.napa.pk)
        self.assertEqual(prices.rows[0]["cost"], Decimal("1.5000"))

    def test_profit_reports_need_their_own_permission(self):
        staff = add_staff(self.tenant, "r@x.test", ["report.list", "sale.list"])
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("reports:index")), "Product profit")
        self.assertEqual(self.client.get(reverse("reports:report", args=["product-profit"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:report", args=["daily-sales"])).status_code, 200)
        self.assertNotIn("profit", self.client.get(reverse("core:dashboard")).context["kpi"])
        self.client.force_login(add_staff(self.tenant, "n@x.test", ["pos.create"]))
        self.assertEqual(self.client.get(reverse("reports:index")).status_code, 403)

    def test_dashboard(self):
        context = self.client.get(reverse("core:dashboard")).context
        self.assertEqual((context["kpi"]["today"], context["kpi"]["today_count"]), (Decimal("70"), 2))
        self.assertEqual(len(context["chart"]), 30)
        self.assertEqual(context["chart"][-1]["total"], 80.0)
        self.assertEqual(context["top_sellers"][0]["units"], 40)
        self.assertEqual(context["receivable"]["total"], Decimal("25"))
        self.assertEqual(context["payable"]["total"], Decimal("30"))
