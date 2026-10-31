import os
import zlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import PaymentAccount
from apps.accounts.models import User
from apps.billing.models import Plan
from apps.catalog.models import CatalogMedicine
from apps.catalog.services import adopt, import_catalog
from apps.core.tenancy import tenant_context
from apps.customers.models import Customer
from apps.inventory.models import Transfer, WriteOff
from apps.inventory.services import create_write_off, dispatch_transfer, receive_transfer, save_stock_count
from apps.items.models import Medicine, Unit
from apps.outlets.models import Counter, Outlet
from apps.people.models import Employee, Role
from apps.purchases.models import Purchase
from apps.purchases.services import clean_lines, receive, save_draft
from apps.sales.models import Promotion, Quotation, Sale
from apps.sales.services import complete_sale, create_quotation, create_return, start_register
from apps.setup.services import provision_tenant
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
                self._lazz_sales(tenant)
                self._lazz_inventory(tenant)

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

    def _lazz_sales(self, tenant):
        """Customers, an open register for the owner and a few of today's sales (one on due, partly returned)."""
        with tenant_context(tenant), transaction.atomic():
            if Sale.objects.exists():
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
