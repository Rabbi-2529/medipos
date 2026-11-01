import os
import zlib
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounting.chart import ensure_chart
from apps.accounting.models import Account, CashEntry, FundMovement, PaymentAccount
from apps.accounting.posting import rebuild
from apps.accounting.services import move_funds, record_cash_entry
from apps.accounts.models import User
from apps.audit.context import paused as audit_paused
from apps.billing.models import Plan
from apps.catalog.models import CatalogMedicine
from apps.catalog.services import adopt, import_catalog
from apps.core.tenancy import tenant_context
from apps.customers.models import Customer
from apps.hr.models import Attendance, OfficeNetwork, Payroll, Shift
from apps.hr.services import give_advance, pay_payroll, prepare_payroll, save_attendance
from apps.inventory.models import Transfer, WriteOff
from apps.inventory.services import create_write_off, dispatch_transfer, receive_transfer, save_stock_count
from apps.items.models import Medicine, Unit
from apps.outlets.models import Counter, Outlet
from apps.people.models import Employee, Role
from apps.purchases.models import Purchase
from apps.purchases.services import clean_lines, receive, save_draft
from apps.sales.models import Promotion, Quotation, Sale
from apps.sales.services import clean_cart, complete_sale, create_quotation, create_return, start_register, totals
from apps.setup.services import get_business_settings, provision_tenant
from apps.stock.models import BatchStock, StockMovement
from apps.stock.services import batch_on_hand, get_or_create_batch, post_movement
from apps.suppliers.models import Supplier
from apps.suppliers.services import record_payment, set_opening_balance
from apps.tenants.models import Tenant

SAMPLE_CATALOG = Path(__file__).resolve().parents[3] / "catalog" / "fixtures" / "sample_catalog.csv"
RX_GENERICS = {"Azithromycin", "Cefixime", "Clonazepam", "Metronidazole"}

# One pharmacy per subscription situation, so every screen has something to show.
DEMO_TENANTS = [
    {
        "slug": "lazz-pharma-demo",
        "name": "Lazz Pharma (Demo)",
        "phone": "01700000000",
        "plan": "business",
        "state": "paid",
        "owner": {"email": "owner@lazzpharma.local", "phone": "01700000001", "first_name": "Lazz", "last_name": "Owner"},
    },
    {
        "slug": "green-life-demo",
        "name": "Green Life Pharmacy (Demo)",
        "phone": "01800000000",
        "plan": "starter",
        "state": "trial_ending",
        "owner": {"email": "owner@greenlife.local", "phone": "01800000001", "first_name": "Green", "last_name": "Owner"},
    },
    {
        "slug": "sunrise-demo",
        "name": "Sunrise Pharmacy (Demo)",
        "phone": "01900000000",
        "plan": "starter",
        "state": "expired",
        "owner": {"email": "owner@sunrise.local", "phone": "01900000001", "first_name": "Sunrise", "last_name": "Owner"},
    },
]


def _subscription_fields(state, now):
    today = timezone.localdate(now)
    if state == "paid":
        return {"status": Tenant.Status.ACTIVE, "trial_ends_at": now - timedelta(days=40), "paid_until": today + timedelta(days=25)}
    if state == "trial_ending":
        return {"status": Tenant.Status.TRIAL, "trial_ends_at": now + timedelta(days=2), "paid_until": None}
    return {"status": Tenant.Status.TRIAL, "trial_ends_at": now - timedelta(days=3), "paid_until": None}


class Command(BaseCommand):
    help = "Create a platform admin and three demo pharmacies (paid, trial ending, expired). Dev only; safe to re-run."

    def handle(self, *args, **options):
        # Demo data isn't anyone's doing: keep it out of the audit log.
        with audit_paused():
            self.seed()

    def seed(self):
        password = os.environ.get("DEMO_PASSWORD")
        if not password:
            raise CommandError("Set DEMO_PASSWORD in .env first (see .env.example).")

        self._ensure_user(
            "admin@medipos.local",
            password,
            first_name="Platform",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )
        now = timezone.now()
        for spec in DEMO_TENANTS:
            tenant, created = Tenant.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "phone": spec["phone"],
                    "plan": Plan.objects.get(slug=spec["plan"]),
                    **_subscription_fields(spec["state"], now),
                },
            )
            if not tenant.plan_id:  # pharmacies seeded before plans existed
                tenant.plan = Plan.objects.get(slug=spec["plan"])
                for field, value in _subscription_fields(spec["state"], now).items():
                    setattr(tenant, field, value)
                tenant.save()
            owner = self._ensure_user(spec["owner"]["email"], password, tenant=tenant, is_owner=True, **{
                k: v for k, v in spec["owner"].items() if k != "email"
            })
            if not owner.is_owner:
                owner.is_owner = True
                owner.save(update_fields=["is_owner"])
            with tenant_context(tenant):
                Outlet.objects.get_or_create(code="0001", defaults={"name": "Main branch", "phone": spec["phone"]})
            provision_tenant(tenant)
            if spec["slug"] == "lazz-pharma-demo":
                self._lazz_staff(tenant, password)
                self._lazz_medicines(tenant)
                self._lazz_stock(tenant)
                self._lazz_history(tenant)
                self._lazz_sales(tenant)
                self._lazz_inventory(tenant)
                self._lazz_accounts(tenant)
                self._lazz_shifts(tenant)
                self._lazz_hr(tenant)
                self._lazz_messaging(tenant)

        self.stdout.write(self.style.SUCCESS(
            "Demo ready: admin@medipos.local (platform), owner@lazzpharma.local (paid), "
            "manager@lazzpharma.local + cashier@lazzpharma.local (Lazz staff), "
            "owner@greenlife.local (trial ending), owner@sunrise.local (expired). Password = DEMO_PASSWORD from .env."
        ))

    def _lazz_medicines(self, tenant):
        """Sample catalog (brands only, no prices) adopted by Lazz with made-up DEMO prices and packs."""
        if not CatalogMedicine.objects.exists():
            data = SAMPLE_CATALOG.read_bytes()
            import_catalog(SimpleUploadedFile(SAMPLE_CATALOG.name, data, content_type="text/csv"))
        with tenant_context(tenant):
            adopt(tenant, list(CatalogMedicine.objects.filter(is_active=True)))
            units = {u.name: u for u in Unit.objects.all()}
            for medicine in Medicine.objects.filter(sale_price=0).select_related("generic"):
                # Demo-only prices, derived from the name so re-runs give the same numbers.
                seed = zlib.crc32(medicine.display_name.encode()) % 1000
                if medicine.dosage_form in ("tablet", "capsule"):
                    medicine.purchase_unit, medicine.conversion, medicine.strip_size = units["Box"], 100, 10
                    medicine.purchase_price = Decimal(50 + seed) / 100  # ৳0.50–10.49 a piece
                elif medicine.dosage_form == "sachet":
                    medicine.purchase_unit, medicine.conversion = units["Box"], 20
                    medicine.purchase_price = Decimal(400 + seed) / 100
                else:
                    medicine.sale_unit = medicine.purchase_unit = units["Bottle"]
                    medicine.purchase_price = Decimal(4000 + seed * 5) / 100
                medicine.sale_price = (medicine.purchase_price * Decimal("1.18")).quantize(Decimal("0.01"))
                medicine.alert_quantity = 50 if medicine.conversion > 1 else 5
                medicine.prescription_required = bool(medicine.generic and medicine.generic.name in RX_GENERICS)
                medicine.save()

    def _lazz_stock(self, tenant):
        """Suppliers, opening stock at Main branch, one received + part-paid purchase and one draft (once)."""
        with tenant_context(tenant):
            if Purchase.objects.exists():
                return
            owner = tenant.owner
            main = Outlet.objects.get(code="0001")
            square = Supplier.objects.create(name="Square Pharmaceuticals (Depot)", phone="01711000111", contact_person="Rahim")
            beximco = Supplier.objects.create(name="Beximco Distribution", phone="01811000222", opening_balance=Decimal("2500"))
            set_opening_balance(beximco)
            today = timezone.localdate()
            for index, medicine in enumerate(Medicine.objects.filter(catalog_ref__isnull=False).order_by("name")):
                expiry = today.replace(day=1) + timedelta(days=60 + index * 35)  # a few expire within 90 days
                batch = get_or_create_batch(medicine, f"OP{index + 1:03d}", expiry, medicine.purchase_price)
                quantity = medicine.conversion * (2 + index % 4) + (index * 7) % 13
                post_movement(outlet=main, batch=batch, quantity=quantity, kind=StockMovement.Kind.OPENING,
                              ref=("opening", None, "Opening stock"), user=owner)

            def lines(names, packs):
                rows = []
                for name in names:
                    m = Medicine.objects.get(name=name, dosage_form__in=["tablet", "capsule"])
                    rows.append({"medicine": m.pk, "batch_no": f"{name[:3].upper()}{today:%y%m}", "packs": packs, "units": 0,
                                 "free_units": m.conversion // 10, "expiry": (today + timedelta(days=540)).strftime("%Y-%m"),
                                 "pack_cost": str(m.purchase_price * m.conversion), "sale_price": str(m.sale_price)})
                return clean_lines(rows)

            received = save_draft(tenant=tenant, outlet=main, supplier=square, date=today - timedelta(days=3),
                                  lines=lines(["Seclo", "Fexo", "Alatrol"], 2), supplier_invoice_no="SQ-55120", user=owner)
            received = receive(received, user=owner)
            record_payment(supplier=square, amount=(received.total / 2).quantize(Decimal("1")), user=owner,
                           account=PaymentAccount.objects.get(name="Cash"), date=today - timedelta(days=3), purchase=received)
            save_draft(tenant=tenant, outlet=main, supplier=beximco, date=today, lines=lines(["Napa", "Tofen"], 3), user=owner)

    def _lazz_history(self, tenant):
        """Four weeks of walk-in counter sales before today, so the dashboard and reports have a shape (once)."""
        with tenant_context(tenant), transaction.atomic():
            if Sale.objects.exists():
                return
            owner, main = tenant.owner, Outlet.objects.get(code="0001")
            cash, bkash = PaymentAccount.objects.get(name="Cash"), PaymentAccount.objects.get(name="bKash")
            medicines = list(Medicine.objects.filter(catalog_ref__isnull=False, dosage_form__in=["tablet", "capsule"]).order_by("name"))
            settings, now = get_business_settings(tenant), timezone.now()
            for days_back in range(28, 0, -1):
                day = timezone.localtime(now - timedelta(days=days_back))
                for k in range(2 + (days_back * 7) % 4):  # 2–5 sales a day
                    seed = days_back * 31 + k * 17
                    lines = [{"medicine": medicines[(seed + j * 5) % len(medicines)].pk, "unit": "strip" if (seed + j) % 2 else "piece",
                              "qty": 1 + (seed + j) % 3} for j in range(1 + seed % 3)]
                    total = totals(clean_cart(lines, "retail"), cart_discount=0, settings=settings)["total"]
                    account = bkash if seed % 4 == 0 else cash
                    complete_sale(tenant=tenant, outlet=main, user=owner, lines=lines, rx_checked=True,
                                  payments=[{"account": account.pk, "amount": str(total)}],
                                  now=day.replace(hour=9 + 2 * k, minute=(seed * 7) % 60))

    def _lazz_sales(self, tenant):
        """Customers, an open register for the owner and a few of today's sales (one on due, partly returned)."""
        with tenant_context(tenant), transaction.atomic():
            if Customer.objects.filter(phone="01711223344").exists():
                return
            owner, main = tenant.owner, Outlet.objects.get(code="0001")
            cash, bkash = PaymentAccount.objects.get(name="Cash"), PaymentAccount.objects.get(name="bKash")
            rahim = Customer.objects.create(name="Rahim Uddin", phone="01711223344", credit_limit=Decimal("5000"), default_discount=Decimal("5"))
            Customer.objects.create(name="Fatema Begum", phone="01822334455")
            register = start_register(owner, main, "2000")
            medicines = {m.name: m for m in Medicine.objects.filter(catalog_ref__isnull=False)}

            def sell(items, payments, **kw):
                return complete_sale(tenant=tenant, outlet=main, register=register, user=owner, payments=payments,
                                     lines=[{"medicine": medicines[name].pk, "unit": unit, "qty": qty} for name, unit, qty in items], **kw)

            def pay_all(items, account=cash):
                sale_total = sum(medicines[n].sale_price * qty * (medicines[n].strip_size or 1 if u == "strip" else 1) for n, u, qty in items)
                return [{"account": account.pk, "amount": str(sale_total.quantize(Decimal("1")))}]

            sell([("Napa", "strip", 2), ("Ceevit", "piece", 10)], pay_all([("Napa", "strip", 2), ("Ceevit", "piece", 10)]))
            sell([("Seclo", "strip", 1)], pay_all([("Seclo", "strip", 1)], bkash))
            on_due = sell([("Monas", "piece", 10), ("Fexo", "piece", 10)], [{"account": cash.pk, "amount": "100"}], customer=rahim)
            sell([("Alatrol", "strip", 1)], pay_all([("Alatrol", "strip", 1)]))
            fexo = on_due.lines.get(medicine=medicines["Fexo"])
            # Rahim brings back 4 Fexo: credited to his account (nothing paid out).
            create_return(on_due, {fexo.pk: 4}, date=timezone.localdate(), reason="Doctor changed the prescription",
                          register=register, user=owner)

    def _lazz_inventory(self, tenant):
        """Expired and low stock, transfers to Gulshan (one on the way), a write-off, a stock count, promotions, quotations."""
        with tenant_context(tenant), transaction.atomic():
            if Transfer.objects.exists():
                return
            owner, main, gulshan = tenant.owner, Outlet.objects.get(code="0001"), Outlet.objects.get(code="0002")
            today = timezone.localdate()

            def med(name, *forms):
                return Medicine.objects.filter(name=name, dosage_form__in=forms or ["tablet", "capsule"]).first()

            def first_batch(medicine):
                return BatchStock.objects.filter(outlet=main, medicine=medicine, quantity__gt=0).select_related("batch").first().batch

            napa, seclo, fexo, ceevit, monas = med("Napa"), med("Seclo"), med("Fexo"), med("Ceevit"), med("Monas")
            # Two batches that ran out of date on the shelf, for the expired-stock screen.
            for medicine, days, qty in ((monas, 20, 30), (fexo, 5, 12)):
                batch = get_or_create_batch(medicine, f"EXP{days:02d}", today - timedelta(days=days), medicine.purchase_price)
                post_movement(outlet=main, batch=batch, quantity=qty, kind=StockMovement.Kind.OPENING, ref=("opening", None, "Opening stock"), user=owner)
            # Two medicines below their alert at Main.
            Medicine.objects.filter(pk__in=[seclo.pk, ceevit.pk]).update(alert_quantity=500)

            received = dispatch_transfer(tenant=tenant, from_outlet=main, to_outlet=gulshan, user=owner, note="Weekly top-up",
                                         lines=[(first_batch(fexo), 100), (first_batch(napa), 100)])
            receive_transfer(received, user=owner)
            dispatch_transfer(tenant=tenant, from_outlet=main, to_outlet=gulshan, user=owner, note="Urgent: Seclo ran out",
                              lines=[(first_batch(seclo), 50)])
            create_write_off(tenant=tenant, outlet=main, date=today, reason=WriteOff.Reason.DAMAGED, user=owner,
                             note="Strips torn in the drawer", lines=[(first_batch(napa), 10)])
            ceevit_batch = first_batch(ceevit)
            save_stock_count(tenant=tenant, outlet=main, date=today, user=owner, note="Monthly count, rack A",
                             lines=[(ceevit_batch, batch_on_hand(main, ceevit_batch) - 3)])

            Promotion.objects.create(name="Syrup week", kind=Promotion.Kind.PERCENT, value=Decimal("10"),
                                              scope=Promotion.Scope.FORM, dosage_form="suspension", ends_on=today + timedelta(days=6))
            bulk = Promotion.objects.create(name="Napa by the strip", kind=Promotion.Kind.AMOUNT, value=Decimal("0.20"),
                                            scope=Promotion.Scope.MEDICINES, min_quantity=10)
            bulk.medicines.add(napa)
            Promotion.objects.create(name="Winter sachets", kind=Promotion.Kind.PERCENT, value=Decimal("5"),
                                     scope=Promotion.Scope.FORM, dosage_form="sachet", starts_on=today + timedelta(days=7))

            fatema = Customer.objects.get(phone="01822334455")
            cart = [{"medicine": napa.pk, "unit": "strip", "qty": 3}, {"medicine": ceevit.pk, "unit": "piece", "qty": 20}]
            create_quotation(tenant=tenant, outlet=main, user=owner, lines=cart, customer=fatema, valid_days=7,
                             note="Monthly medicines for her mother")
            old = create_quotation(tenant=tenant, outlet=main, user=owner, lines=[{"medicine": seclo.pk, "unit": "strip", "qty": 2}],
                                   valid_days=3)
            Quotation.objects.filter(pk=old.pk).update(date=today - timedelta(days=10), valid_until=today - timedelta(days=7))

    def _lazz_accounts(self, tenant):
        """A bank account, this month's bills and a little other income, money moved around; then the books from scratch."""
        with tenant_context(tenant), transaction.atomic():
            if not CashEntry.objects.exists():
                owner, main = tenant.owner, Outlet.objects.get(code="0001")
                # The money the shop already had when it started using the app.
                PaymentAccount.objects.filter(name="Cash").update(opening_balance=Decimal("20000"))
                PaymentAccount.objects.filter(name="bKash").update(opening_balance=Decimal("5000"))
                cash, bkash = PaymentAccount.objects.get(name="Cash"), PaymentAccount.objects.get(name="bKash")
                bank, _created = PaymentAccount.objects.get_or_create(name="Dutch-Bangla Bank", defaults={
                    "kind": PaymentAccount.Kind.BANK, "provider": "Dutch-Bangla Bank", "account_number": "103-110-55821",
                    "opening_balance": Decimal("50000"), "sort_order": 3})
                ensure_chart()
                today = timezone.localdate()
                category = {a.code: a for a in Account.objects.filter(code__in=["6020", "6030", "6040", "6060", "4910"])}
                for code, account, amount, note, days in (
                    ("6020", bank, "850", "DESCO electricity bill", 3), ("6030", bkash, "500", "Internet, October", 2),
                    ("6060", cash, "250", "Medicine bags and labels", 1), ("6040", cash, "120", "Rickshaw for a delivery", 0),
                ):
                    record_cash_entry(tenant=tenant, kind=CashEntry.Kind.EXPENSE, date=today - timedelta(days=days), account=category[code],
                                      payment_account=account, amount=amount, outlet=main, note=note, user=owner)
                record_cash_entry(tenant=tenant, kind=CashEntry.Kind.INCOME, date=today, account=category["4910"], payment_account=bank,
                                  amount="300", note="Square: target bonus", user=owner)
                move_funds(tenant=tenant, kind=FundMovement.Kind.DEPOSIT, date=today - timedelta(days=4), amount="10000", to_account=bank,
                           note="Owner added working capital", user=owner)
                move_funds(tenant=tenant, kind=FundMovement.Kind.TRANSFER, date=today, amount="300", from_account=cash, to_account=bank,
                           note="Cash deposited at the bank", user=owner)
                move_funds(tenant=tenant, kind=FundMovement.Kind.WITHDRAW, date=today, amount="200", from_account=bkash,
                           note="Owner's personal use", user=owner)
            rebuild()

    def _lazz_hr(self, tenant):
        """Pay for the staff, a delivery rider with no login, attendance since last month, an advance,
        last month's payroll paid and this month's waiting as a draft (once). Anything already entered by hand is kept."""
        with tenant_context(tenant), transaction.atomic():
            if Employee.objects.filter(name="Karim Mia", user=None).exists():
                return
            owner = tenant.owner
            manager = Employee.objects.get(user__email="manager@lazzpharma.local")
            cashier = Employee.objects.get(user__email="cashier@lazzpharma.local")
            for employee, salary, allowance, commission in ((manager, "25000", "2000", "0"), (cashier, "15000", "1000", "1")):
                if not employee.salary:
                    employee.salary, employee.allowance, employee.commission_percent = Decimal(salary), Decimal(allowance), Decimal(commission)
                    employee.save()
            today = timezone.localdate()
            rider = Employee.objects.create(name="Karim Mia", designation="Delivery rider", phone="01900112233", salary=Decimal("8000"),
                                            joined_on=today - timedelta(days=200))
            if not Sale.objects.filter(created_by=cashier.user).exists():
                # The cashier rang up every third counter sale of the last four weeks (their commission comes from these).
                history = list(Sale.objects.filter(register__isnull=True, created_by=owner).order_by("pk").values_list("pk", flat=True))
                Sale.objects.filter(pk__in=history[::3]).update(created_by=cashier.user)

            this_month = today.replace(day=1)
            last_month = (this_month - timedelta(days=1)).replace(day=1)
            S, day = Attendance.Status, last_month
            marked = set(Attendance.objects.values_list("employee_id", "date"))
            while day <= today:
                rows = []
                for i, employee in enumerate((manager, cashier, rider)):
                    if (day == today and employee is rider) or (employee.pk, day) in marked:
                        continue  # not in yet, or already marked by hand
                    seed = day.toordinal() * 7 + i * 13
                    status = (S.OFF if day.weekday() == 4 else S.ABSENT if seed % 23 == 0 else S.HALF if seed % 29 == 0
                              else S.LEAVE if seed % 37 == 0 else S.LATE if seed % 6 == 0 else S.PRESENT)
                    worked = status in (S.PRESENT, S.LATE, S.HALF)
                    # Out a little after their last shift ends; now and then over an hour before it (marked as leaving early).
                    end = datetime.combine(day, employee.schedule.end if employee.schedule else time(21, 0))
                    out = end - timedelta(minutes=75) if seed % 11 == 0 else end + timedelta(minutes=seed % 25)
                    rows.append({"employee": employee, "status": status,
                                 "check_in": time(9, 35 + seed % 20) if status == S.LATE else time(8, 50 + seed % 10) if worked else None,
                                 "check_out": (time(14, 0) if status == S.HALF else out.time()) if worked and day < today else None,
                                 "note": "Sick" if status == S.LEAVE else ""})
                save_attendance(day=day, rows=rows, user=manager.user)
                day += timedelta(days=1)

            cash, bank = PaymentAccount.objects.get(name="Cash"), PaymentAccount.objects.get(name="Dutch-Bangla Bank")
            if not Payroll.objects.filter(month=last_month).exists():
                give_advance(tenant=tenant, employee=cashier, date=last_month + timedelta(days=12), amount=Decimal("1500"), payment_account=cash,
                             note="Family emergency", user=owner)
                paid = prepare_payroll(tenant=tenant, month=last_month, user=owner)
                pay_payroll(paid, account=bank, day=this_month, user=owner)
            if not Payroll.objects.filter(month=this_month).exists():
                give_advance(tenant=tenant, employee=cashier, date=this_month + timedelta(days=9), amount=Decimal("2000"), payment_account=cash,
                             note="Eid shopping", user=owner)
                prepare_payroll(tenant=tenant, month=this_month, user=owner)

    def _lazz_shifts(self, tenant):
        """Day and evening shifts (the cashier works both), and the networks staff can check in from without a reason (once).
        127.0.0.1 is there so checking in on a local demo works; the /24 stands in for a branch's broadband."""
        with tenant_context(tenant), transaction.atomic():
            if Shift.objects.exists() or OfficeNetwork.objects.exists():
                return
            day = Shift.objects.create(name="Day", start=time(9, 0), end=time(17, 0), grace_minutes=10)
            evening = Shift.objects.create(name="Evening", start=time(15, 0), end=time(23, 0), grace_minutes=10)
            Employee.objects.get(user__email="manager@lazzpharma.local").shifts.set([day])
            Employee.objects.get(user__email="cashier@lazzpharma.local").shifts.set([day, evening])
            OfficeNetwork.objects.create(address="127.0.0.1", label="This computer (demo)")
            OfficeNetwork.objects.create(address="103.112.54.0/24", label="Main branch broadband")

    def _lazz_messaging(self, tenant):
        """SMS credit; bills by SMS, due reminders and the daily alert switched on; loyalty points from past bills;
        a few messages in the outbox and two campaigns (one sent). Once: skipped when the pharmacy ever had SMS credit."""
        from apps.messaging import loyalty
        from apps.messaging.models import Campaign, Channel, SmsCredit
        from apps.messaging.services import add_credits, due_customers, message_settings, send_bill, send_campaign, send_due_reminders

        with tenant_context(tenant), transaction.atomic():
            if SmsCredit.objects.exists():
                return
            owner = tenant.owner
            ms = message_settings()
            ms.invoice_sms = ms.due_reminders = ms.daily_alerts = True
            ms.alert_phones, ms.alert_emails, ms.email_reply_to = tenant.phone or owner.phone or "", owner.email, tenant.email or ""
            ms.save()
            add_credits(tenant, credits=500, amount=Decimal("175"), note="Starter pack (demo)", user=owner)
            program = loyalty.program()
            if not program.enabled:
                program.enabled = True
                program.save()
            for name, phone in (("Nasima Akter", "01933445566"), ("Shahidul Islam", "01544556677"), ("Tania Rahman", "01655667788")):
                Customer.objects.get_or_create(phone=phone, defaults={"name": name})
            for sale in Sale.objects.filter(customer__isnull=False, points_earned=0, points_redeemed=0).select_related("customer").order_by("sold_at"):
                loyalty.record_sale(sale, user=owner)
            for customer in Customer.objects.filter(points=0, is_active=True).exclude(phone="").order_by("name")[:3]:
                loyalty.adjust(customer, 60, note="Welcome points (demo)", user=owner)
            for sale in Sale.objects.filter(customer__isnull=False).select_related("customer", "outlet").order_by("-sold_at")[:3]:
                send_bill(sale, channel=Channel.SMS, user=owner)
            send_due_reminders(list(due_customers(ms)), user=owner)
            Campaign.objects.create(name="Winter flu care", channel=Channel.SMS, created_by=owner,
                                    text="Dear {name}, flu season is here. Napa, Fexo and masks are in stock at {shop}. Call {shop_phone}.")
            eid = Campaign.objects.create(name="Eid greetings", channel=Channel.SMS, created_by=owner,
                                          text="Eid Mubarak, {name}! You have {points_balance} points to spend at {shop}.")
            send_campaign(eid, user=owner)

    def _lazz_staff(self, tenant, password):
        """Second outlet, a counter, a manager (all outlets) and a cashier (Main branch only)."""
        with tenant_context(tenant):
            main = Outlet.objects.get(code="0001")
            Outlet.objects.get_or_create(code="0002", defaults={"name": "Gulshan branch", "phone": "01700000002"})
            Counter.objects.get_or_create(outlet=main, name="Counter 1")
            for email, first, role_key, all_outlets in (
                ("manager@lazzpharma.local", "Lazz Manager", "manager", True),
                ("cashier@lazzpharma.local", "Lazz Cashier", "cashier", False),
            ):
                user = self._ensure_user(email, password, tenant=tenant, first_name=first)
                employee, created = Employee.objects.get_or_create(user=user, defaults={
                    "role": Role.objects.get(template_key=role_key), "all_outlets": all_outlets,
                    "designation": "Manager" if role_key == "manager" else "Cashier",
                })
                if created and not all_outlets:
                    employee.outlets.set([main])

    def _ensure_user(self, email, password, **fields):
        user, created = User.objects.get_or_create(email=email, defaults=fields)
        if created:
            user.set_password(password)
            user.save()
        return user
