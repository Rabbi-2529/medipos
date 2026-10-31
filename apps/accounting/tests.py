from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy, make_supplier
from apps.customers.models import Customer
from apps.customers.services import balance as customer_balance
from apps.customers.services import collect
from apps.outlets.models import Outlet
from apps.purchases.services import clean_lines, create_return as return_to_supplier, receive, save_draft
from apps.sales.services import complete_sale, create_return, register_summary, start_register
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement
from apps.suppliers.services import balance as supplier_balance
from apps.suppliers.services import record_payment

from . import posting, reports
from .chart import money_account, system_account
from .models import Account, CashEntry, FundMovement, JournalEntry, PaymentAccount
from .services import AccountingError, create_manual_entry, delete_cash_entry, move_funds, record_cash_entry

TODAY = timezone.localdate()


class PaymentAccountTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def _create(self, **data):
        payload = {"name": "DBBL", "kind": "bank", "provider": "Dutch-Bangla Bank", "account_number": "123-456",
                   "opening_balance": "5000", "sort_order": "5"}
        payload.update(data)
        return self.client.post(reverse("accounting:payment_account_create"), payload)

    def test_list_and_create(self):
        response = self.client.get(reverse("accounting:payment_accounts"))
        self.assertContains(response, "bKash")
        self.assertContains(response, "৳0.00")
        self.assertRedirects(self._create(), reverse("accounting:payment_accounts"))
        with tenant_context(self.tenant):
            self.assertEqual(PaymentAccount.objects.get(name="DBBL").opening_balance, 5000)

    def test_duplicate_name_is_a_form_error(self):
        self.assertContains(self._create(name="Cash"), "already exists")

    def test_keeps_one_active_account(self):
        with tenant_context(self.tenant):
            accounts = list(PaymentAccount.objects.all())
        for account in accounts[:-1]:
            self.client.post(reverse("accounting:payment_account_toggle", args=[account.pk]))
        response = self.client.post(reverse("accounting:payment_account_toggle", args=[accounts[-1].pk]), follow=True)
        self.assertContains(response, "at least one active payment account")


class BooksTests(TestCase):
    """Every document posts a balanced entry, and the ledger agrees with the sub-ledgers and the register."""

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=10, purchase_price="8.00", sale_price="1.20")
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.outlet = Outlet.objects.get()
        self.cash, self.bkash = PaymentAccount.objects.get(name="Cash"), PaymentAccount.objects.get(name="bKash")
        batch = get_or_create_batch(self.napa, "B1", TODAY + timedelta(days=300), Decimal("0.80"))
        post_movement(outlet=self.outlet, batch=batch, quantity=100, kind=StockMovement.Kind.OPENING)
        posting.post_opening_stock(self.outlet)
        self.register = start_register(self.owner, self.outlet, "500")

    def balance(self, account):
        return reports.balance_of(account)

    def sell(self, qty, payments, **kw):
        return complete_sale(tenant=self.tenant, outlet=self.outlet, register=self.register, user=self.owner,
                             lines=[{"medicine": self.napa.pk, "qty": qty}], payments=payments, **kw)

    def test_cash_sale_and_opening_stock(self):
        self.sell(10, [{"account": self.cash.pk, "amount": 12}])
        self.assertEqual(self.balance(money_account(self.cash)), Decimal("12"))
        self.assertEqual(self.balance(system_account("sales")), Decimal("12"))
        self.assertEqual(self.balance(system_account("cogs")), Decimal("8.00"))
        self.assertEqual(self.balance(system_account("inventory")), Decimal("72.00"))  # 80 opening - 8 sold
        self.assertTrue(reports.trial_balance(TODAY)["balanced"])

    def test_due_sale_return_and_collection_match_the_customer_ledger(self):
        rahim = Customer.objects.create(name="Rahim", phone="017")
        sale = self.sell(50, [{"account": self.cash.pk, "amount": 20}], customer=rahim)  # 60 total, 40 due
        create_return(sale, {sale.lines.get().pk: 5}, date=TODAY, refund_amount=2, refund_account=self.cash, register=self.register)
        collect(rahim, amount=Decimal("10"), account=self.bkash, date=TODAY, sale=sale)
        self.assertEqual(self.balance(system_account("receivable")), customer_balance(rahim))
        self.assertEqual(self.balance(system_account("sales_returns")), Decimal("-6"))  # contra income: a debit
        self.assertTrue(reports.trial_balance(TODAY)["balanced"])

    def test_purchases_payments_and_returns_match_the_supplier_ledger(self):
        supplier = make_supplier(self.tenant)
        purchase = save_draft(tenant=self.tenant, outlet=self.outlet, supplier=supplier, date=TODAY, user=self.owner,
                              lines=clean_lines([{"medicine": self.napa.pk, "batch_no": "P1", "expiry": (TODAY + timedelta(days=400)).strftime("%Y-%m"),
                                                  "packs": 3, "units": 0, "free_units": 0, "pack_cost": "100", "sale_price": "12"}]))
        purchase = receive(purchase, user=self.owner)
        record_payment(supplier=supplier, amount=Decimal("100"), account=self.cash, date=TODAY, purchase=purchase)
        return_to_supplier(purchase, {purchase.lines.get().pk: 5}, date=TODAY)
        self.assertEqual(self.balance(system_account("payable")), supplier_balance(supplier))
        self.assertEqual(self.balance(system_account("inventory")), Decimal("80") + Decimal("300") - Decimal("50"))
        self.assertTrue(reports.trial_balance(TODAY)["balanced"])

    def test_cash_in_the_books_matches_the_register(self):
        start = self.balance(money_account(self.cash))
        self.sell(10, [{"account": self.cash.pk, "amount": 12}])
        self.sell(5, [{"account": self.bkash.pk, "amount": 6}])
        rent = Account.objects.get(code="6000")
        record_cash_entry(tenant=self.tenant, kind=CashEntry.Kind.EXPENSE, date=TODAY, account=rent, payment_account=self.cash,
                          amount="7.50", register=self.register)
        move_funds(tenant=self.tenant, kind=FundMovement.Kind.WITHDRAW, date=TODAY, amount="2", from_account=self.cash, register=self.register)
        summary = register_summary(self.register)
        self.assertEqual(summary["expected_cash"], Decimal("500") + Decimal("12") - Decimal("7.50") - Decimal("2"))
        self.assertEqual(self.balance(money_account(self.cash)) - start, summary["expected_cash"] - self.register.opening_cash)
        self.assertEqual(summary["expenses"], Decimal("7.50"))

    def test_profit_and_loss_and_balance_sheet(self):
        self.sell(20, [{"account": self.cash.pk, "amount": 24}])
        record_cash_entry(tenant=self.tenant, kind=CashEntry.Kind.EXPENSE, date=TODAY, account=Account.objects.get(code="6020"),
                          payment_account=self.cash, amount="5")
        record_cash_entry(tenant=self.tenant, kind=CashEntry.Kind.INCOME, date=TODAY, account=Account.objects.get(code="4920"),
                          payment_account=self.bkash, amount="3")
        move_funds(tenant=self.tenant, kind=FundMovement.Kind.DEPOSIT, date=TODAY, amount="1000", to_account=self.bkash)
        pnl = reports.profit_and_loss(TODAY, TODAY)
        self.assertEqual((pnl["net_sales"], pnl["cogs"], pnl["gross"], pnl["net"]),
                         (Decimal("24"), Decimal("16.00"), Decimal("8.00"), Decimal("6.00")))
        sheet = reports.balance_sheet(TODAY)
        self.assertTrue(sheet["balanced"])
        self.assertEqual(sheet["profit"], Decimal("6.00"))
        flow = reports.cash_flow(TODAY, TODAY)
        self.assertEqual((flow["in"], flow["out"]), (Decimal("1027"), Decimal("5")))

    def test_deleting_a_voucher_removes_its_entry_and_rebuild_keeps_the_books(self):
        entry = record_cash_entry(tenant=self.tenant, kind=CashEntry.Kind.EXPENSE, date=TODAY, account=Account.objects.get(code="6000"),
                                  payment_account=self.cash, amount="10")
        delete_cash_entry(entry)
        self.assertFalse(JournalEntry.objects.filter(source_type="expense").exists())
        self.sell(10, [{"account": self.cash.pk, "amount": 12}])
        manual = create_manual_entry(tenant=self.tenant, date=TODAY, memo="Shelf bought on loan", lines=[
            (Account.objects.create(code="1500", name="Furniture", type=Account.Type.ASSET), "5000", "0", ""),
            (Account.objects.create(code="2500", name="Loan", type=Account.Type.LIABILITY), "0", "5000", ""),
        ])
        before = reports.trial_balance(TODAY)
        posting.rebuild()
        after = reports.trial_balance(TODAY)
        self.assertEqual([(r["account"].pk, r["debit"], r["credit"]) for r in before["rows"]],
                         [(r["account"].pk, r["debit"], r["credit"]) for r in after["rows"]])
        self.assertTrue(JournalEntry.objects.filter(pk=manual.pk).exists())  # manual entries survive a rebuild

    def test_refused_entries(self):
        rent, cash = Account.objects.get(code="6000"), money_account(self.cash)
        with self.assertRaisesMessage(AccountingError, "must be equal"):
            create_manual_entry(tenant=self.tenant, date=TODAY, memo="x", lines=[(rent, "10", "0", ""), (cash, "0", "9", "")])
        with self.assertRaisesMessage(AccountingError, "income category"):
            record_cash_entry(tenant=self.tenant, kind=CashEntry.Kind.INCOME, date=TODAY, account=rent, payment_account=self.cash, amount="1")
        with self.assertRaisesMessage(AccountingError, "two different accounts"):
            move_funds(tenant=self.tenant, kind=FundMovement.Kind.TRANSFER, date=TODAY, amount="1", from_account=self.cash, to_account=self.cash)


class AccountingPageTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.cash = PaymentAccount.objects.get(name="Cash")
            self.bank = PaymentAccount.objects.create(name="DBBL", kind="bank")
            self.rent = Account.objects.get(code="6000")

    def test_vouchers_and_reports(self):
        response = self.client.post(reverse("accounting:expenses"), {"date": TODAY.isoformat(), "account": self.rent.pk,
                                                                     "payment_account": self.cash.pk, "amount": "1500", "note": "October rent"})
        self.assertRedirects(response, reverse("accounting:expenses"))
        response = self.client.post(reverse("accounting:transfers"), {"kind": "deposit", "date": TODAY.isoformat(), "to_account": self.bank.pk,
                                                                      "amount": "20000"})
        self.assertRedirects(response, reverse("accounting:transfers"))
        self.assertContains(self.client.get(reverse("accounting:expenses")), "October rent")
        self.assertContains(self.client.get(reverse("accounting:payment_accounts")), "−৳1,500.00")
        for name in ("chart", "trial_balance", "profit_loss", "balance_sheet", "cash_flow", "statement", "journal", "entry_create"):
            self.assertEqual(self.client.get(reverse(f"accounting:{name}")).status_code, 200, name)
        self.assertContains(self.client.get(reverse("accounting:profit_loss")), "Shop rent")
        self.assertContains(self.client.get(reverse("accounting:balance_sheet")), "Assets = liabilities + equity")
        data = self.client.get(reverse("accounting:journal_data"), {"draw": 1, "start": 0, "length": 10}).json()
        self.assertEqual(data["recordsTotal"], 2)

    def test_manual_entry_form(self):
        with tenant_context(self.tenant):
            capital = Account.objects.get(system_key="capital")
            ledger = money_account(self.bank)
        import json
        response = self.client.post(reverse("accounting:entry_create"), {"date": TODAY.isoformat(), "memo": "Opening bank balance",
                                                                         "lines": json.dumps([{"account": ledger.pk, "debit": "100"},
                                                                                              {"account": capital.pk, "credit": "100"}])})
        with tenant_context(self.tenant):
            entry = JournalEntry.objects.get(memo="Opening bank balance")
        self.assertRedirects(response, reverse("accounting:entry", args=[entry.pk]))
        self.client.post(reverse("accounting:entry_delete", args=[entry.pk]))
        with tenant_context(self.tenant):
            self.assertFalse(JournalEntry.objects.filter(pk=entry.pk).exists())

    def test_cashier_cannot_open_the_books(self):
        cashier = add_staff(self.tenant, "c@x.test", ["pos.create", "expense.create"])
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("accounting:trial_balance")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounting:expenses")).status_code, 403)  # needs expense.list too


class CategoryTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_add_rename_hide_and_quick_add(self):
        url = reverse("accounting:expense_categories")
        self.assertContains(self.client.get(url), "Shop rent")
        self.assertRedirects(self.client.post(url, {"name": "Security guard"}), url)
        self.assertContains(self.client.post(url, {"name": "security GUARD"}, follow=True), "already exists")
        with tenant_context(self.tenant):
            guard = Account.objects.get(name="Security guard")
            self.assertEqual((guard.type, guard.code), (Account.Type.EXPENSE, "6910"))  # after "Other expenses" (6900)
        self.client.post(reverse("accounting:expense_category_edit", args=[guard.pk]), {"name": "Night guard", "description": "Paid weekly"})
        self.client.post(reverse("accounting:expense_category_toggle", args=[guard.pk]))
        form = self.client.get(reverse("accounting:expenses")).context["form"]
        self.assertNotIn("Night guard", [a.name for a in form.fields["account"].queryset])  # hidden from new entries
        quick = self.client.post(reverse("accounting:income_category_quick"), {"name": "Rent from upstairs room"}, content_type="application/json")
        self.assertEqual(quick.json()["text"], "Rent from upstairs room")
        with tenant_context(self.tenant):
            self.assertEqual(Account.objects.get(pk=quick.json()["id"]).type, Account.Type.INCOME)
        self.assertEqual(self.client.post(reverse("accounting:income_category_quick"), {"name": " "}, content_type="application/json").status_code, 400)

    def test_system_categories_stay_put_and_staff_need_permission(self):
        with tenant_context(self.tenant):
            other_income = Account.objects.get(system_key="other_income")
        self.client.post(reverse("accounting:income_category_edit", args=[other_income.pk]), {"name": "Renamed"})
        other_income.refresh_from_db()
        self.assertEqual(other_income.label, "Other income")
        self.assertNotContains(self.client.get(reverse("accounting:expense_categories")), "Cost of medicines sold")
        viewer = add_staff(self.tenant, "v@x.test", ["expense.list"])
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get(reverse("accounting:expense_categories")), "Add category")
        self.assertEqual(self.client.post(reverse("accounting:expense_category_quick"), {"name": "X"}, content_type="application/json").status_code, 403)
