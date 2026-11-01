import io
import json
import zipfile
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounting.models import PaymentAccount
from apps.accounts.models import User
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.customers.models import Customer
from apps.messaging.services import message_settings
from apps.outlets.models import Outlet
from apps.people.models import Role
from apps.sales.services import complete_sale, start_register
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from .models import ExportLog

TODAY = timezone.localdate()


def content(response):
    return b"".join(response.streaming_content)


class ExportTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.other, _ = make_pharmacy("Rival Pharma")
        with tenant_context(self.other):
            Customer.objects.create(name="Not yours", phone="01999999999")
        with tenant_context(self.tenant):
            outlet = Outlet.objects.get()
            cash = PaymentAccount.objects.get(name="Cash")
            napa = make_medicine(self.tenant, "Napa", sale_price="10.00")
            batch = get_or_create_batch(napa, "B1", TODAY + timedelta(days=300), Decimal("5"))
            post_movement(outlet=outlet, batch=batch, quantity=100, kind=StockMovement.Kind.OPENING)
            register = start_register(self.owner, outlet, "0")
            self.rahim = Customer.objects.create(name="Rahim", phone="01712345678")
            for days_ago in (0, 400):  # one sale now, one long ago
                complete_sale(tenant=self.tenant, outlet=outlet, user=self.owner, register=register, customer=self.rahim,
                              lines=[{"medicine": napa.pk, "qty": 2}], payments=[{"account": cash.pk, "amount": "20"}],
                              now=timezone.now() - timedelta(days=days_ago))
            ms = message_settings()
            ms.sms_api_key = "SECRET-KEY-123"
            ms.save()
        self.client.force_login(self.owner)

    def test_excel_workbook(self):
        response = self.client.post(reverse("backup:data"), {"action": "excel", "datasets": ["customers", "sales", "medicines"],
                                                             "start": (TODAY - timedelta(days=30)).isoformat(), "end": TODAY.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertIn(".xlsx", response["Content-Disposition"])
        book = load_workbook(io.BytesIO(content(response)))
        self.assertEqual(book.sheetnames, ["Medicines", "Customers", "Sales"])  # in the page's order
        customers = list(book["Customers"].iter_rows(values_only=True))
        self.assertIn("Owes now", customers[0])
        self.assertEqual([row[0] for row in customers[1:]], ["Rahim"])  # not the other pharmacy's customer
        self.assertEqual(len(list(book["Sales"].iter_rows())), 2)  # the header and this month's sale only
        medicines = list(book["Medicines"].iter_rows(values_only=True))
        self.assertEqual(medicines[1][medicines[0].index("In stock (units)")], 96)
        log = ExportLog.all_objects.get(tenant=self.tenant)
        self.assertEqual((log.kind, log.datasets, log.rows), ("excel", ["medicines", "customers", "sales"], 3))
        self.assertContains(self.client.post(reverse("backup:data"), {"action": "excel", "start": TODAY, "end": TODAY}), "Tick at least one list.")

    def test_full_backup(self):
        response = self.client.post(reverse("backup:data"), {"action": "backup"})
        archive = zipfile.ZipFile(io.BytesIO(content(response)))
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual((manifest["pharmacy"]["slug"], manifest["records"]["sales.sale"]), (self.tenant.slug, 2))
        self.assertIn("data/customers.customer.json", names)
        customers = json.loads(archive.read("data/customers.customer.json"))
        self.assertEqual([c["fields"]["name"] for c in customers], ["Rahim"])
        everything = b"".join(archive.read(name) for name in names)
        self.assertNotIn(b"Not yours", everything)
        self.assertNotIn(b"SECRET-KEY-123", everything)
        users = json.loads(archive.read("data/accounts.user.json"))
        self.assertEqual(users[0]["fields"]["email"], self.owner.email)
        self.assertNotIn("password", users[0]["fields"])
        self.assertEqual(ExportLog.all_objects.get(tenant=self.tenant).kind, "backup")

    def test_only_the_owner_by_default(self):
        with tenant_context(self.tenant):
            manager_role = Role.objects.get(template_key="manager")
        self.assertNotIn("backup.create", manager_role.permissions)
        manager = add_staff(self.tenant, "m@x.test", manager_role.permissions, all_outlets=True)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("backup:data")).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("backup:data")), "Download full backup")

    def test_platform_support_backup(self):
        admin = User.objects.create_superuser("root@x.test", "pw")
        self.client.force_login(admin)
        response = self.client.post(reverse("platform:tenant_backup", args=[self.tenant.pk]))
        self.assertIn(self.tenant.slug, response["Content-Disposition"])
        self.assertIn("manifest.json", zipfile.ZipFile(io.BytesIO(content(response))).namelist())
        self.assertEqual(ExportLog.all_objects.get(tenant=self.tenant).created_by, admin)
