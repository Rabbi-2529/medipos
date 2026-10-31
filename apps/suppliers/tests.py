from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import make_medicine, make_pharmacy, make_supplier
from apps.outlets.models import Outlet
from apps.purchases.services import clean_lines, receive, save_draft

from .models import Supplier, SupplierLedgerEntry
from .services import SupplierError, balance, record_payment, set_opening_balance, with_balances


class LedgerTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.supplier = Supplier.objects.create(name="ACI Distribution", opening_balance=Decimal("500"))
        set_opening_balance(self.supplier)
        self.cash = PaymentAccount.objects.get(name="Cash")

    def _received_purchase(self, total_packs=1):
        napa = make_medicine(self.tenant, f"Med{total_packs}", conversion=1, track_expiry=False)
        outlet = Outlet.objects.get()
        return receive(save_draft(tenant=self.tenant, outlet=outlet, supplier=self.supplier, date=timezone.localdate(),
                                  lines=clean_lines([{"medicine": napa.pk, "packs": total_packs, "pack_cost": "100"}])))

    def test_opening_balance_entry_follows_supplier(self):
        self.assertEqual(balance(self.supplier), Decimal("500"))
        self.supplier.opening_balance = Decimal("-50")
        set_opening_balance(self.supplier)
        self.assertEqual(balance(self.supplier), Decimal("-50"))
        self.supplier.opening_balance = 0
        set_opening_balance(self.supplier)
        self.assertFalse(SupplierLedgerEntry.objects.exists())

    def test_payments(self):
        purchase = self._received_purchase(3)  # 300
        payment = record_payment(supplier=self.supplier, amount=Decimal("120"), account=self.cash, date=timezone.localdate(), purchase=purchase)
        self.assertEqual(payment.number, "SPY-00001")
        self.assertEqual(purchase.due_amount(), Decimal("180"))
        self.assertEqual(balance(self.supplier), Decimal("680"))
        with self.assertRaisesMessage(SupplierError, "Only 180.00 is due"):
            record_payment(supplier=self.supplier, amount=Decimal("181"), account=self.cash, date=timezone.localdate(), purchase=purchase)
        record_payment(supplier=self.supplier, amount=Decimal("680"), account=self.cash, date=timezone.localdate())  # on account
        self.assertEqual(balance(self.supplier), Decimal("0"))
        self.assertEqual(with_balances(Supplier.objects.filter(pk=self.supplier.pk)).get().balance, Decimal("0"))

    def test_payment_rules(self):
        other = Supplier.objects.create(name="Other")
        purchase = self._received_purchase()
        with self.assertRaisesMessage(SupplierError, "another supplier"):
            record_payment(supplier=other, amount=Decimal("1"), account=self.cash, date=timezone.localdate(), purchase=purchase)
        with self.assertRaises(SupplierError):
            record_payment(supplier=self.supplier, amount=Decimal("0"), account=self.cash, date=timezone.localdate())


class SupplierScreenTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_create_with_opening_balance_and_list(self):
        self.client.post(reverse("suppliers:create"), {"name": "Renata Depot", "contact_person": "", "phone": "017", "email": "",
                                                       "address": "", "opening_balance": "1500"})
        make_supplier(self.tenant, "Zero Ltd")
        data = self.client.get(reverse("suppliers:data"), {"draw": 1, "start": 0, "length": 10, "due": "yes", "status": "active"}).json()
        self.assertEqual(data["recordsTotal"], 1)
        self.assertIn("৳1,500.00", str(data["data"]))
        with tenant_context(self.tenant):
            renata = Supplier.objects.get(name="Renata Depot")
        self.assertContains(self.client.get(reverse("suppliers:detail", args=[renata.pk])), "Opening balance")

    def test_payment_form_and_scoped_lookup(self):
        supplier = make_supplier(self.tenant, "Square Depot", opening_balance=Decimal("300"))
        with tenant_context(self.tenant):
            set_opening_balance(supplier)
            cash = PaymentAccount.objects.get(name="Cash")
        response = self.client.post(reverse("suppliers:payment_create"), {
            "supplier": supplier.pk, "purchase": "", "amount": "100", "account": cash.pk,
            "date": timezone.localdate().isoformat(), "reference": "TX1", "note": "",
        })
        self.assertRedirects(response, reverse("suppliers:detail", args=[supplier.pk]))
        with tenant_context(self.tenant):
            self.assertEqual(balance(supplier), Decimal("200"))
        other, _ = make_pharmacy("Green Life")
        make_supplier(other, "Square Depot Two")
        results = self.client.get(reverse("suppliers:lookup"), {"q": "square"}).json()["results"]
        self.assertEqual([r["text"] for r in results], ["Square Depot"])
