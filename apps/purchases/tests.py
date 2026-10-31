import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy, make_supplier
from apps.outlets.models import Outlet
from apps.stock.models import StockMovement
from apps.stock.services import on_hand, post_movement
from apps.suppliers.models import SupplierPayment
from apps.suppliers.services import balance

from .models import Purchase
from .services import PurchaseError, clean_lines, create_return, receive, save_draft

NEXT_YEAR = (timezone.localdate() + timedelta(days=400)).strftime("%Y-%m")


def line(medicine, **values):
    data = {"medicine": medicine.pk, "batch_no": "B1", "expiry": NEXT_YEAR, "packs": 2, "units": 5, "free_units": 0,
            "pack_cost": "100", "sale_price": "12"}
    data.update(values)
    return data


class PurchaseServiceTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, conversion=10)  # box of 10
        self.supplier = make_supplier(self.tenant)
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
            self.cash = PaymentAccount.objects.get(name="Cash")
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def _draft(self, *lines, discount="0", update_prices=True):
        return save_draft(tenant=self.tenant, outlet=self.outlet, supplier=self.supplier, date=timezone.localdate(),
                          lines=clean_lines(list(lines)), discount=Decimal(discount), update_prices=update_prices, user=self.owner)

    def test_line_validation(self):
        with self.assertRaisesMessage(PurchaseError, "pick a medicine"):
            clean_lines([{"medicine": 999999}])
        with self.assertRaisesMessage(PurchaseError, "enter a quantity"):
            clean_lines([line(self.napa, packs=0, units=0)])
        with self.assertRaisesMessage(PurchaseError, "expiry date is required"):
            clean_lines([line(self.napa, expiry="")])
        with self.assertRaisesMessage(PurchaseError, "already expired"):
            clean_lines([line(self.napa, expiry="2020-01")])
        with self.assertRaisesMessage(PurchaseError, "can't be negative"):
            clean_lines([line(self.napa, packs=-1)])
        with self.assertRaisesMessage(PurchaseError, "Add at least one"):
            clean_lines([])

    def test_totals_and_discount(self):
        purchase = self._draft(line(self.napa), discount="10")  # 2 boxes + 5 pieces at 100/box = 250
        self.assertEqual((purchase.subtotal, purchase.total, purchase.number), (Decimal("250.00"), Decimal("240.00"), "PUR-00001"))
        with self.assertRaisesMessage(PurchaseError, "Discount"):
            self._draft(line(self.napa), discount="300")

    def test_receive_posts_stock_cost_prices_and_ledger(self):
        purchase = self._draft(line(self.napa, free_units=5))  # 25 paid + 5 free = 30 pieces for 250
        receive(purchase, user=self.owner)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.RECEIVED)
        self.assertEqual(on_hand(self.outlet, self.napa), 30)
        batch = purchase.lines.get().batch
        self.assertEqual((batch.batch_no, batch.unit_cost), ("B1", Decimal("8.3333")))  # 250 / 30
        self.napa.refresh_from_db()
        self.assertEqual((self.napa.purchase_price, self.napa.sale_price), (Decimal("10.0000"), Decimal("12.00")))
        self.assertEqual(balance(self.supplier), Decimal("250.00"))
        with self.assertRaisesMessage(PurchaseError, "already received"):
            receive(purchase)

    def test_prices_untouched_when_asked(self):
        receive(self._draft(line(self.napa), update_prices=False))
        self.napa.refresh_from_db()
        self.assertEqual(self.napa.sale_price, Decimal("1.20"))

    def test_return_reduces_stock_and_balance(self):
        purchase = receive(self._draft(line(self.napa)))  # 25 pieces at 10 each
        purchase_line = purchase.lines.get()
        purchase_return = create_return(purchase, {purchase_line.pk: 5}, date=timezone.localdate(), reason="Damaged")
        self.assertEqual((purchase_return.number, purchase_return.total), ("PRT-00001", Decimal("50.00")))
        self.assertEqual(on_hand(self.outlet, self.napa), 20)
        self.assertEqual(balance(self.supplier), Decimal("200.00"))
        self.assertEqual(purchase.due_amount(), Decimal("200.00"))
        with self.assertRaisesMessage(PurchaseError, "at most 20"):
            create_return(purchase, {purchase_line.pk: 21}, date=timezone.localdate())

    def test_return_refused_when_stock_already_gone(self):
        purchase = receive(self._draft(line(self.napa)))
        purchase_line = purchase.lines.get()
        post_movement(outlet=self.outlet, batch=purchase_line.batch, quantity=-24, kind=StockMovement.Kind.SALE)
        with self.assertRaisesMessage(PurchaseError, "1 left, 3 needed"):
            create_return(purchase, {purchase_line.pk: 3}, date=timezone.localdate())
        self.assertFalse(purchase.returns.exists())
        self.assertEqual(balance(self.supplier), Decimal("250.00"))


class PurchaseScreenTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, conversion=10)
        self.supplier = make_supplier(self.tenant)
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()
            self.gulshan = Outlet.objects.create(name="Gulshan", code="0002")
            self.cash = PaymentAccount.objects.get(name="Cash")
        self.client.force_login(self.owner)

    def _post(self, lines, url=None, **fields):
        data = {"supplier": self.supplier.pk, "outlet": self.main.pk, "date": timezone.localdate().isoformat(),
                "supplier_invoice_no": "INV-9", "discount": "0", "update_prices": "on", "note": "", "paid_now": "",
                "account": self.cash.pk, "lines_json": json.dumps(lines), "receive": "1"}
        data.update(fields)
        return self.client.post(url or reverse("purchases:create"), {k: v for k, v in data.items() if v is not None})

    def test_create_receive_and_pay(self):
        response = self._post([line(self.napa)], paid_now="100")
        self.assertEqual(response.status_code, 302, response.context and response.context["form"].errors)
        with tenant_context(self.tenant):
            purchase = Purchase.objects.get()
            self.assertRedirects(response, reverse("purchases:detail", args=[purchase.pk]))
            self.assertEqual(on_hand(self.main, self.napa), 25)
            self.assertEqual(purchase.due_amount(), Decimal("150.00"))
            self.assertEqual(SupplierPayment.objects.get().account, self.cash)
        detail = self.client.get(reverse("purchases:detail", args=[purchase.pk]))
        self.assertContains(detail, "2 Box + 5 Piece")

    def test_errors_keep_lines_and_save_nothing(self):
        response = self._post([line(self.napa, expiry="")])
        self.assertContains(response, "expiry date is required")
        self.assertEqual(response.context["lines"][0]["medicine"], self.napa.pk)
        with tenant_context(self.tenant):
            self.assertFalse(Purchase.objects.exists())

    def test_draft_edit_then_receive_and_delete_rules(self):
        self._post([line(self.napa)], receive=None, save="1")
        with tenant_context(self.tenant):
            draft = Purchase.objects.get()
        self.assertEqual(draft.status, "draft")
        self._post([line(self.napa, packs=5, units=0)], url=reverse("purchases:edit", args=[draft.pk]), receive=None, save="1")
        draft.refresh_from_db()
        self.assertEqual(draft.total, Decimal("500.00"))
        self.client.post(reverse("purchases:receive", args=[draft.pk]))
        with tenant_context(self.tenant):
            self.assertEqual(on_hand(self.main, self.napa), 50)
        self.assertRedirects(self.client.get(reverse("purchases:edit", args=[draft.pk])), reverse("purchases:detail", args=[draft.pk]))
        response = self.client.post(reverse("purchases:delete", args=[draft.pk]), follow=True)
        self.assertContains(response, "Only drafts can be deleted")

    def test_return_screen(self):
        self._post([line(self.napa)])
        with tenant_context(self.tenant):
            purchase = Purchase.objects.get()
            line_pk = purchase.lines.get().pk
        self.assertContains(self.client.get(reverse("purchases:return_create", args=[purchase.pk])), "Can return")
        self.client.post(reverse("purchases:return_create", args=[purchase.pk]), {"date": timezone.localdate().isoformat(), "reason": "", f"qty_{line_pk}": "5"})
        with tenant_context(self.tenant):
            self.assertEqual(on_hand(self.main, self.napa), 20)

    def test_staff_limited_to_their_outlet(self):
        self._post([line(self.napa)], outlet=self.gulshan.pk)
        with tenant_context(self.tenant):
            at_gulshan = Purchase.objects.get()
        staff = add_staff(self.tenant, "s@x.test", ["purchase.list", "purchase.create"], outlets=[self.main])
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("purchases:detail", args=[at_gulshan.pk])).status_code, 404)
        response = self._post([line(self.napa)], outlet=self.gulshan.pk)
        self.assertContains(response, "Select a valid choice")

    def test_cashier_cannot_buy(self):
        self.client.force_login(add_staff(self.tenant, "c@x.test", ["stock.list"]))
        self.assertEqual(self.client.get(reverse("purchases:create")).status_code, 403)

    def test_due_lookup(self):
        self._post([line(self.napa)], paid_now="50")
        data = self.client.get(reverse("purchases:due_lookup"), {"supplier": self.supplier.pk}).json()
        self.assertEqual(data["results"][0]["due"], "200.00")
        self.assertIn("৳200.00", data["balance_text"])
