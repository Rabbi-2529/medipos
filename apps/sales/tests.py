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
from apps.customers.services import balance
from apps.items.models import Category
from apps.outlets.models import Outlet
from apps.setup.services import get_business_settings
from apps.stock.models import StockMovement
from apps.stock.services import batch_on_hand, get_or_create_batch, on_hand, post_movement

from .models import Promotion, Quotation, Sale
from .services import (
    SaleError, close_register, complete_sale, create_return, hold_cart, recall_cart, register_summary, start_register,
)

TODAY = timezone.localdate()


class SaleSetup(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=100, sale_price="1.20", strip_size=10, barcode="8901")
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.outlet = Outlet.objects.get()
        self.cash = PaymentAccount.objects.get(name="Cash")
        self.bkash = PaymentAccount.objects.get(name="bKash")
        self.soon = self._stock(self.napa, "SOON", TODAY + timedelta(days=30), 15, "0.90")
        self.late = self._stock(self.napa, "LATE", TODAY + timedelta(days=400), 100, "1.00")
        self.old = self._stock(self.napa, "OLD", TODAY - timedelta(days=1), 50, "0.80")  # expired: never sold

    def _stock(self, medicine, batch_no, expiry, quantity, cost):
        batch = get_or_create_batch(medicine, batch_no, expiry, Decimal(cost))
        post_movement(outlet=self.outlet, batch=batch, quantity=quantity, kind=StockMovement.Kind.OPENING)
        return batch

    def sell(self, lines, payments=None, **kwargs):
        return complete_sale(tenant=self.tenant, outlet=self.outlet, user=self.owner, lines=lines, payments=payments or [], **kwargs)


class SellingTests(SaleSetup):
    def test_fefo_skips_expired_and_records_cost(self):
        sale = self.sell([{"medicine": self.napa.pk, "qty": 20}], [{"account": self.cash.pk, "amount": 24}])
        self.assertEqual(sale.number, "INV00001")
        self.assertEqual((batch_on_hand(self.outlet, self.soon), batch_on_hand(self.outlet, self.late), batch_on_hand(self.outlet, self.old)), (0, 95, 50))
        self.assertEqual(sale.cost_total, Decimal("18.5"))  # 15 × 0.90 + 5 × 1.00
        self.assertEqual((sale.total, sale.paid, sale.due), (Decimal("24"), Decimal("24"), Decimal("0")))

    def test_expired_stock_is_never_enough(self):
        with self.assertRaisesMessage(SaleError, "only 115 in stock that hasn't expired, 116 needed"):
            self.sell([{"medicine": self.napa.pk, "qty": 116}])
        self.assertEqual(on_hand(self.outlet, self.napa), 165)
        self.assertFalse(Sale.objects.exists())

    def test_chosen_batch_and_units(self):
        sale = self.sell([{"medicine": self.napa.pk, "unit": "strip", "qty": 2, "batch": self.late.pk}], [{"account": self.cash.pk, "amount": 24}])
        line = sale.lines.get()
        self.assertEqual((line.unit_label, line.quantity, line.line_total), ("Strip", 20, Decimal("24.00")))
        self.assertEqual(batch_on_hand(self.outlet, self.late), 80)
        with self.assertRaisesMessage(SaleError, "the chosen batch"):
            self.sell([{"medicine": self.napa.pk, "qty": 1, "batch": self.old.pk}])

    def test_rounding_vat_and_discounts(self):
        settings = get_business_settings(self.tenant)
        settings.vat_percent = Decimal("5")
        settings.save()
        # 13 × 1.20 = 15.60, line discount 0.60 -> 15.00, cart discount 1.00 -> 14.00, VAT 0.70 -> 14.70, rounded 15.
        sale = self.sell([{"medicine": self.napa.pk, "qty": 13, "discount": "0.60"}], [{"account": self.cash.pk, "amount": 15}], cart_discount="1")
        self.assertEqual((sale.subtotal, sale.vat_amount, sale.rounding, sale.total), (Decimal("15.60"), Decimal("0.70"), Decimal("0.30"), Decimal("15")))

    def test_split_payment_and_overpay(self):
        sale = self.sell([{"medicine": self.napa.pk, "qty": 10}], [{"account": self.cash.pk, "amount": 5}, {"account": self.bkash.pk, "amount": 7}])
        self.assertEqual(sorted(p.account.name for p in sale.payments.all()), ["Cash", "bKash"])
        with self.assertRaisesMessage(SaleError, "more than the bill"):
            self.sell([{"medicine": self.napa.pk, "qty": 1}], [{"account": self.cash.pk, "amount": 5}])

    def test_due_needs_customer_within_limit(self):
        with self.assertRaisesMessage(SaleError, "Pick a customer"):
            self.sell([{"medicine": self.napa.pk, "qty": 10}])
        rahim = Customer.objects.create(name="Rahim", phone="017", credit_limit=Decimal("20"))
        sale = self.sell([{"medicine": self.napa.pk, "qty": 10}], [{"account": self.cash.pk, "amount": 2}], customer=rahim)
        self.assertEqual(balance(rahim), Decimal("10"))
        self.assertEqual(sale.due, Decimal("10"))
        with self.assertRaisesMessage(SaleError, "may owe at most"):
            self.sell([{"medicine": self.napa.pk, "qty": 10}], customer=rahim)

    def test_prescription_needs_check(self):
        rx = make_medicine(self.tenant, "Zimax", conversion=1, sale_price="40", prescription_required=True, code="ZMX")
        self._stock(rx, "Z1", TODAY + timedelta(days=200), 5, "30")
        with self.assertRaisesMessage(SaleError, "prescription"):
            self.sell([{"medicine": rx.pk, "qty": 1}], [{"account": self.cash.pk, "amount": 40}])
        self.assertTrue(self.sell([{"medicine": rx.pk, "qty": 1}], [{"account": self.cash.pk, "amount": 40}], rx_checked=True).rx_checked)

    def test_wholesale_price(self):
        self.napa.wholesale_price = Decimal("1.00")
        self.napa.save()
        sale = self.sell([{"medicine": self.napa.pk, "qty": 10}], [{"account": self.cash.pk, "amount": 10}], price_type="wholesale")
        self.assertEqual(sale.total, Decimal("10"))


class ReturnTests(SaleSetup):
    def test_walk_in_return_goes_back_to_same_batches_with_full_refund(self):
        sale = self.sell([{"medicine": self.napa.pk, "qty": 20}], [{"account": self.cash.pk, "amount": 24}])
        line = sale.lines.get()
        with self.assertRaisesMessage(SaleError, "full"):
            create_return(sale, {line.pk: 10}, date=TODAY, refund_amount=0)
        sale_return = create_return(sale, {line.pk: 10}, date=TODAY, refund_amount=12, refund_account=self.cash)
        self.assertEqual(sale_return.total, Decimal("12.00"))
        # The 5 from LATE come back first, then 5 into SOON.
        self.assertEqual((batch_on_hand(self.outlet, self.late), batch_on_hand(self.outlet, self.soon)), (100, 5))
        with self.assertRaisesMessage(SaleError, "at most 10"):
            create_return(sale, {line.pk: 11}, date=TODAY, refund_amount=0)

    def test_refund_shares_cart_discount_and_credits_customer(self):
        rahim = Customer.objects.create(name="Rahim", phone="017")
        sale = self.sell([{"medicine": self.napa.pk, "qty": 10}], [], customer=rahim, cart_discount="2")  # 12 - 2 = 10 on due
        line = sale.lines.get()
        sale_return = create_return(sale, {line.pk: 5}, date=TODAY, refund_amount=0)
        self.assertEqual(sale_return.total, Decimal("5.00"))  # half the goods = half of what was charged
        self.assertEqual(balance(rahim), Decimal("5"))


class RegisterAndHoldTests(SaleSetup):
    def test_register_expected_cash(self):
        register = start_register(self.owner, self.outlet, "500")
        with self.assertRaisesMessage(SaleError, "already open"):
            start_register(self.owner, self.outlet, "0")
        self.sell([{"medicine": self.napa.pk, "qty": 10}], [{"account": self.cash.pk, "amount": 8}, {"account": self.bkash.pk, "amount": 4}], register=register)
        summary = register_summary(register)
        self.assertEqual((summary["sale_count"], summary["expected_cash"]), (1, Decimal("508")))
        closed = close_register(register, "505")
        self.assertEqual((closed.expected_cash, closed.counted_cash), (Decimal("508"), Decimal("505")))
        start_register(self.owner, self.outlet, "0")  # can open again after closing

    def test_hold_and_recall(self):
        held = hold_cart(outlet=self.outlet, user=self.owner, data={"lines": [{"medicine": self.napa.pk}]}, label="Rahim")
        self.assertEqual(recall_cart(self.outlet, held.pk)["lines"][0]["medicine"], self.napa.pk)
        with self.assertRaises(SaleError):
            recall_cart(self.outlet, held.pk)


class PosEndpointTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=100, sale_price="1.20", barcode="8901")
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
            self.cash = PaymentAccount.objects.get(name="Cash")
            batch = get_or_create_batch(self.napa, "B1", TODAY + timedelta(days=300), Decimal("1"))
            post_movement(outlet=self.outlet, batch=batch, quantity=50, kind=StockMovement.Kind.OPENING)
        self.client.force_login(self.owner)

    def _post(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_register_gate_search_checkout_receipt(self):
        self.assertContains(self.client.get(reverse("sales:pos")), "Open your cash register")
        self.client.post(reverse("sales:register_open"), {"opening_cash": "100"})
        self.assertContains(self.client.get(reverse("sales:pos")), "pos-config")
        found = self.client.get(reverse("sales:pos_search"), {"q": "8901"}).json()
        self.assertEqual((found["exact"], found["results"][0]["stock"]), (True, 50))
        result = self._post("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 5}],
                                                     "payments": [{"account": self.cash.pk, "amount": 6}], "cashTendered": 10})
        data = result.json()
        self.assertEqual((data["number"], data["change"]), ("INV00001", "৳4.00"))
        for size in ("58", "80", "a4"):
            self.assertContains(self.client.get(data["receiptUrl"], {"size": size}), "INV00001")
        bad = self._post("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 999}], "payments": []})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("only 45 in stock", bad.json()["error"])

    def test_full_screen_pos_browses_best_sellers_and_categories(self):
        with tenant_context(self.tenant):
            fever = Category.objects.create(name="Fever")
        ace = make_medicine(self.tenant, "Ace", category=fever, dosage_form="tablet")  # never stocked
        seclo = make_medicine(self.tenant, "Seclo", category=fever, dosage_form="capsule")
        with tenant_context(self.tenant):
            batch = get_or_create_batch(seclo, "S1", TODAY + timedelta(days=300), Decimal("1"))
            post_movement(outlet=self.outlet, batch=batch, quantity=80, kind=StockMovement.Kind.OPENING)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        page = self.client.get(reverse("sales:pos"))
        self.assertNotContains(page, "app-sidebar")
        self.assertContains(page, f'data-category="{fever.pk}"')   # Napa has no category, so Fever really narrows the list
        self.assertContains(page, 'data-category="form:capsule"')

        def browse(**params):
            data = self.client.get(reverse("sales:pos_search"), params).json()
            return [r["id"] for r in data["results"]], data.get("hasSales"), data.get("more")

        # No sales yet: the shelf, most stock first.
        self.assertEqual(browse(), ([seclo.pk, self.napa.pk], False, False))
        self._post("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 5}], "payments": [{"account": self.cash.pk, "amount": 6}]})
        self.assertEqual(browse(category="top"), ([self.napa.pk, seclo.pk], True, False))
        self.assertEqual(browse(category=fever.pk)[0], [seclo.pk, ace.pk])  # in stock first
        self.assertEqual(browse(category="form:capsule")[0], [seclo.pk])
        self.assertEqual(len(browse(category="all")[0]), 3)
        self.assertEqual(browse(category="nope")[0], [])

    def test_checkout_without_register_is_refused(self):
        response = self._post("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 1}], "payments": []})
        self.assertEqual(response.status_code, 400)

    def test_cashier_can_sell_but_not_buy_and_only_sees_own_registers(self):
        cashier = add_staff(self.tenant, "c@x.test", ["pos.create", "sale.list"], all_outlets=True)
        manager_register = None
        with tenant_context(self.tenant):
            manager_register = start_register(self.owner, self.outlet, "0")
        self.client.force_login(cashier)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        self.assertEqual(self.client.get(reverse("purchases:create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("sales:register", args=[manager_register.pk])).status_code, 404)
        self.assertEqual(len(self.client.get(reverse("sales:registers")).context["registers"]), 1)

    def test_return_screen(self):
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        sale_id = self._post("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 5}],
                                                    "payments": [{"account": self.cash.pk, "amount": 6}]}).json()["id"]
        with tenant_context(self.tenant):
            line = Sale.objects.get(pk=sale_id).lines.get()
        response = self.client.post(reverse("sales:return_create", args=[sale_id]),
                                    {f"qty_{line.pk}": "2", "refund_amount": "2.40", "refund_account": self.cash.pk, "reason": ""})
        self.assertRedirects(response, reverse("sales:detail", args=[sale_id]))
        with tenant_context(self.tenant):
            self.assertEqual(on_hand(self.outlet, self.napa), 47)


class PromotionAndQuotationTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=100, sale_price="2.00", dosage_form="tablet")
        self.syrup = make_medicine(self.tenant, "Tusca", conversion=1, sale_price="80.00", dosage_form="syrup")
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
            self.cash = PaymentAccount.objects.get(name="Cash")
            for medicine in (self.napa, self.syrup):
                batch = get_or_create_batch(medicine, "B1", TODAY + timedelta(days=300), Decimal("1"))
                post_movement(outlet=self.outlet, batch=batch, quantity=50, kind=StockMovement.Kind.OPENING)
            self.syrups = Promotion.objects.create(name="Syrup week", kind="percent", value=Decimal("10"), scope="form", dosage_form="syrup")
            self.bulk = Promotion.objects.create(name="Napa bulk", kind="amount", value=Decimal("0.50"), scope="medicines", min_quantity=10)
            self.bulk.medicines.add(self.napa)
            Promotion.objects.create(name="Old offer", kind="percent", value=Decimal("50"), ends_on=TODAY - timedelta(days=1))
        self.client.force_login(self.owner)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})

    def _post(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_pos_shows_the_best_running_offer(self):
        def offer(term):
            return self.client.get(reverse("sales:pos_search"), {"q": term}).json()["results"][0]["promo"]

        self.assertEqual(offer("Tusca")["name"], "Syrup week")
        self.assertEqual((offer("Napa")["name"], offer("Napa")["minQty"]), ("Napa bulk", 10))  # the expired 50% is ignored
        with tenant_context(self.tenant):
            self.assertEqual(self.bulk.discount(Decimal("2.00"), 9), Decimal("0"))
            self.assertEqual(self.bulk.discount(Decimal("2.00"), 10), Decimal("5.00"))

    def test_sale_records_the_promotion_only_where_it_applies(self):
        sale_id = self._post("sales:pos_checkout", {
            "lines": [{"medicine": self.syrup.pk, "qty": 1, "discount": 8, "promotion": self.syrups.pk},
                      {"medicine": self.napa.pk, "qty": 1, "discount": 0, "promotion": self.syrups.pk}],
            "payments": [{"account": self.cash.pk, "amount": 74}],
        }).json()["id"]
        with tenant_context(self.tenant):
            lines = {l.medicine_id: l.promotion_id for l in Sale.objects.get(pk=sale_id).lines.all()}
        self.assertEqual(lines, {self.syrup.pk: self.syrups.pk, self.napa.pk: None})

    def test_quote_then_sell_it(self):
        quote = self._post("sales:pos_quote", {"lines": [{"medicine": self.syrup.pk, "qty": 2}], "validDays": 3}).json()
        self.assertEqual(quote["number"], "QT-00001")
        with tenant_context(self.tenant):
            quotation = Quotation.objects.get()
            self.assertEqual((quotation.total, quotation.valid_until), (Decimal("160"), TODAY + timedelta(days=3)))
            self.assertEqual(on_hand(self.outlet, self.syrup), 50)  # a quote takes no stock
        self.assertContains(self.client.get(reverse("sales:quotation_print", args=[quotation.pk])), "QT-00001")
        page = self.client.get(reverse("sales:pos"), {"quotation": quotation.pk})
        loaded = page.context["config"]["quotation"]
        self.assertEqual((loaded["number"], [(l["medicine"], l["qty"]) for l in loaded["cart"]["lines"]]), ("QT-00001", [(self.syrup.pk, 2)]))
        sale = self._post("sales:pos_checkout", {"lines": [{"medicine": self.syrup.pk, "qty": 2}], "quotation": quotation.pk,
                                                 "payments": [{"account": self.cash.pk, "amount": 160}]}).json()
        quotation.refresh_from_db()
        self.assertEqual((quotation.status, quotation.sale_id), (Quotation.Status.SOLD, sale["id"]))
        self.assertContains(self.client.get(reverse("sales:quotation", args=[quotation.pk])), sale["number"])

    def test_promotion_form_and_list(self):
        response = self.client.post(reverse("sales:promotion_create"), {
            "name": "Vitamin month", "kind": "percent", "value": "120", "scope": "medicines", "min_quantity": "1", "is_active": "on",
        })
        self.assertFormError(response.context["form"], "value", "A percentage can't be more than 100.")
        self.assertFormError(response.context["form"], "medicines", "Choose what this promotion applies to.")
        response = self.client.post(reverse("sales:promotion_create"), {
            "name": "Vitamin month", "kind": "percent", "value": "5", "scope": "medicines", "medicines": [self.napa.pk],
            "min_quantity": "1", "is_active": "on",
        })
        self.assertRedirects(response, reverse("sales:promotions"))
        self.assertContains(self.client.get(reverse("sales:promotions")), "Vitamin month")
