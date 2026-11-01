"""Selling without internet: the till's copy, and taking in the sales it saved."""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.customers.models import Customer
from apps.outlets.models import Outlet
from apps.stock.models import BatchStock, StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from .models import Sale

TODAY = timezone.localdate()


class OfflineTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=10, sale_price="2.00", barcode="8901")
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
            self.cash = PaymentAccount.objects.get(name="Cash")
            batch = get_or_create_batch(self.napa, "B1", TODAY + timedelta(days=300), Decimal("1"))
            post_movement(outlet=self.outlet, batch=batch, quantity=30, kind=StockMovement.Kind.OPENING)
            self.rahim = Customer.objects.create(name="Rahim", phone="01712345678")
        self.client.force_login(self.owner)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})

    def offline_sale(self, offline_id="till-1", qty=5, minutes_ago=3, **extra):
        sale = {"offlineId": offline_id, "soldAt": (timezone.now() - timedelta(minutes=minutes_ago)).isoformat(), "outlet": self.outlet.pk,
                "lines": [{"medicine": self.napa.pk, "unit": "piece", "qty": qty}], "payments": [{"account": self.cash.pk, "amount": qty * 2}]}
        sale.update(extra)
        return sale

    def sync(self, *sales):
        return self.client.post(reverse("sales:pos_sync"), json.dumps({"sales": list(sales)}), content_type="application/json").json()["results"]

    def test_the_copy_has_what_the_till_needs(self):
        data = self.client.get(reverse("sales:pos_offline_data")).json()
        napa = data["medicines"][0]
        self.assertEqual((napa["id"], napa["stock"], napa["barcode"], napa["price"]), (self.napa.pk, 30, "8901", "2.00"))
        self.assertEqual([c["name"] for c in data["customers"]], ["Rahim"])
        self.assertEqual((data["outlet"], data["tenant"], data["register"] is not None), (self.outlet.pk, self.tenant.pk, True))
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, "pos_offline.js")
        self.assertContains(page, reverse("sales:pos_sync"))

    def test_offline_sales_are_taken_once(self):
        first = self.sync(self.offline_sale(minutes_ago=90, customer=self.rahim.pk))
        self.assertTrue(first[0]["ok"])
        with tenant_context(self.tenant):
            sale = Sale.objects.get()
            stock = BatchStock.objects.get().quantity
        self.assertEqual((sale.number, sale.offline_ref, sale.customer, stock), (first[0]["number"], "till-1", self.rahim, 25))
        self.assertLess(abs((timezone.now() - timedelta(minutes=90) - sale.sold_at).total_seconds()), 5)  # when it was really sold
        self.assertIn("Sold without internet", sale.note)
        again = self.sync(self.offline_sale())  # the till didn't hear back and sends it again
        self.assertEqual((again[0]["ok"], again[0]["already"], again[0]["number"]), (True, True, sale.number))
        with tenant_context(self.tenant):
            self.assertEqual(Sale.objects.count(), 1)

    def test_refusals_come_back_with_the_reason(self):
        results = self.sync(
            self.offline_sale("too-many", qty=99),
            self.offline_sale("future", minutes_ago=-60),
            self.offline_sale("old", minutes_ago=60 * 24 * 40),
            self.offline_sale("no-id", offlineId=""),
            self.offline_sale("fine", qty=2),
        )
        self.assertEqual([r["ok"] for r in results], [False, False, False, False, True])
        self.assertIn("in stock", results[0]["error"])
        self.assertIn("clock", results[1]["error"])
        with tenant_context(self.tenant):
            self.assertEqual(list(Sale.objects.values_list("offline_ref", flat=True)), ["fine"])

    def test_only_outlets_the_person_may_sell_in(self):
        with tenant_context(self.tenant):
            other = Outlet.objects.create(code="0002", name="Gulshan")
        cashier = add_staff(self.tenant, "c@x.test", ["pos.create"], outlets=[self.outlet])
        self.client.force_login(cashier)
        result = self.sync(self.offline_sale("elsewhere", outlet=other.pk))[0]
        self.assertFalse(result["ok"])
        self.assertIn("outlet", result["error"])

    def test_app_files(self):
        worker = self.client.get(reverse("core:service_worker"))
        self.assertEqual(worker["Content-Type"], "application/javascript")
        self.assertEqual(worker["Service-Worker-Allowed"], "/")
        self.assertContains(worker, reverse("sales:pos"))
        manifest = json.loads(self.client.get(reverse("core:manifest")).content)
        self.assertEqual((manifest["start_url"], manifest["display"]), (reverse("sales:pos"), "standalone"))
        self.client.logout()
        self.assertContains(self.client.get(reverse("core:offline")), "No internet")
