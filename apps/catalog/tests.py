from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.core.spreadsheets import XLSX
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import make_pharmacy
from apps.items.models import Medicine

from .models import CatalogMedicine
from .services import adopt, import_catalog


def upload(rows, headers=("Brand name", "Generic", "Strength", "Form", "Company", "Pack", "Price")):
    workbook = Workbook()
    workbook.active.append(list(headers))
    for row in rows:
        workbook.active.append(list(row))
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("catalog.xlsx", buffer.getvalue(), content_type=XLSX)


ROWS = [
    ("Napa", "Paracetamol", "500 mg", "Tab", "Beximco", "10 x 10", 1.2),
    ("Seclo", "Omeprazole", "20 mg", "Cap", "Square", "10 x 6", 6),
    ("Fexo", "Fexofenadine", "120 mg", "Tablet", "Square", "", ""),
]


class CatalogImportTests(TestCase):
    def test_import_creates_then_updates(self):
        created, updated, errors = import_catalog(upload(ROWS + [("", "x", "", "", "", "", "")]))
        self.assertEqual((created, updated), (3, 0))
        self.assertEqual(errors, [(5, "brand name is missing")])
        napa = CatalogMedicine.objects.get(brand_name="Napa")
        self.assertEqual((napa.dosage_form, str(napa.unit_price)), ("tablet", "1.20"))
        created, updated, _ = import_catalog(upload([("Napa", "Paracetamol", "500 mg", "Tab", "Beximco", "10 x 10", 1.5)]))
        self.assertEqual((created, updated), (0, 1))
        napa.refresh_from_db()
        self.assertEqual(str(napa.unit_price), "1.50")


class AdoptTests(TestCase):
    def setUp(self):
        import_catalog(upload(ROWS))
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_adopt_copies_and_skips_duplicates(self):
        rows = list(CatalogMedicine.objects.all())
        with tenant_context(self.tenant):
            self.assertEqual(adopt(self.tenant, rows), (3, 0))
            self.assertEqual(adopt(self.tenant, rows), (0, 3))
            seclo = Medicine.objects.get(name="Seclo")
            self.assertEqual((seclo.generic.name, seclo.manufacturer.name, seclo.dosage_form, str(seclo.sale_price)),
                             ("Omeprazole", "Square", "capsule", "6.00"))
            self.assertIsNotNone(seclo.catalog_ref_id)

    def test_adopt_views(self):
        seclo = CatalogMedicine.objects.get(brand_name="Seclo")
        self.client.post(reverse("items:catalog_adopt"), {"scope": "selected", "ids": [seclo.pk]})
        self.client.post(reverse("items:catalog_adopt"), {"scope": "all", "manufacturer": "square", "q": "", "form": ""})
        with tenant_context(self.tenant):
            self.assertEqual(sorted(Medicine.objects.values_list("name", flat=True)), ["Fexo", "Seclo"])
        data = self.client.get(reverse("items:catalog_data"), {"draw": 1, "start": 0, "length": 25}).json()
        self.assertEqual(data["recordsTotal"], 3)
        self.assertEqual(str(data["data"]).count("Added"), 2)

    def test_adopt_respects_plan_limit(self):
        self.tenant.plan = Plan.objects.create(name="Tiny", slug="tiny", max_medicines=2)
        self.tenant.save()
        response = self.client.post(reverse("items:catalog_adopt"), {"scope": "all", "q": "", "manufacturer": "", "form": ""}, follow=True)
        self.assertContains(response, "plan allows 2 medicines")


class CatalogConsoleTests(TestCase):
    def test_only_platform_admins(self):
        tenant, owner = make_pharmacy()
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("platform:catalog")).status_code, 403)
        self.client.force_login(User.objects.create_superuser("root@x.test", "pw"))
        self.assertEqual(self.client.get(reverse("platform:catalog")).status_code, 200)
        response = self.client.post(reverse("platform:catalog_import"), {"file": upload(ROWS)}, follow=True)
        self.assertContains(response, "3 added, 0 updated")
        data = self.client.get(reverse("platform:catalog_data"), {"draw": 1, "start": 0, "length": 25, "search[value]": "square"}).json()
        self.assertEqual(data["recordsFiltered"], 2)
