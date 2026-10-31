import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.billing.models import Plan
from apps.core.spreadsheets import XLSX
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_pharmacy

from .importer import parse_upload, run_import
from .models import Category, Generic, Manufacturer, Medicine, Rack, Unit
from .services import bulk_create_medicines
from .tools_views import new_price

MEDIA = tempfile.mkdtemp()


def xlsx_upload(headers, rows, name="medicines.xlsx"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=XLSX)


def medicine_form(tenant, **overrides):
    with tenant_context(tenant):
        piece = Unit.objects.get(name="Piece")
        box = Unit.objects.get(name="Box")
    data = {
        "kind": "medicine", "name": "Napa", "strength": "500 mg", "dosage_form": "tablet",
        "generic_name": "Paracetamol", "manufacturer_name": "Beximco", "category_name": "Medicine",
        "alternative_name": "", "track_expiry": "on", "sale_unit": piece.pk, "purchase_unit": box.pk,
        "conversion": "200", "strip_size": "10", "alert_quantity": "50", "rack": "",
        "purchase_price": "0.95", "sale_price": "1.20", "wholesale_price": "", "code": "", "barcode": "", "description": "",
    }
    data.update(overrides)
    return data


@override_settings(MEDIA_ROOT=MEDIA)
class MedicineFormTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_create_numbers_code_and_creates_names(self):
        with tenant_context(self.tenant):
            Generic.objects.create(name="paracetamol")
        response = self.client.post(reverse("items:create"), medicine_form(self.tenant))
        self.assertRedirects(response, reverse("items:list"))
        with tenant_context(self.tenant):
            medicine = Medicine.objects.get()
            self.assertEqual(medicine.code, "000001")
            self.assertEqual(Generic.objects.count(), 1)  # "Paracetamol" reused the existing "paracetamol"
            self.assertEqual((medicine.manufacturer.name, medicine.category.name, medicine.conversion), ("Beximco", "Medicine", 200))
        self.assertEqual(medicine.display_name, "Napa 500 mg Tablet")
        self.assertIn("paracetamol", medicine.search_text)
        self.assertEqual(medicine.margin_percent, Decimal("26.3"))

    def test_same_units_force_conversion_to_one(self):
        with tenant_context(self.tenant):
            piece = Unit.objects.get(name="Piece")
        self.client.post(reverse("items:create"), medicine_form(self.tenant, purchase_unit=piece.pk, conversion="50"))
        with tenant_context(self.tenant):
            self.assertEqual(Medicine.objects.get().conversion, 1)

    def test_duplicate_code_and_barcode_are_form_errors(self):
        self.client.post(reverse("items:create"), medicine_form(self.tenant, code="N1", barcode="111"))
        response = self.client.post(reverse("items:create"), medicine_form(self.tenant, code="N1", barcode="111", name="Napa Extra"))
        self.assertContains(response, "already exists", count=2)

    def test_other_pharmacys_unit_is_rejected(self):
        other, _ = make_pharmacy("Green Life")
        foreign = Unit.all_objects.get(tenant=other, name="Piece")
        response = self.client.post(reverse("items:create"), medicine_form(self.tenant, sale_unit=foreign.pk))
        self.assertContains(response, "Select a valid choice")

    def test_plan_medicine_limit(self):
        tiny = Plan.objects.create(name="Tiny", slug="tiny", max_medicines=1)
        self.tenant.plan = tiny
        self.tenant.save()
        self.client.post(reverse("items:create"), medicine_form(self.tenant))
        response = self.client.post(reverse("items:create"), medicine_form(self.tenant, name="Ace"))
        self.assertContains(response, "plan allows 1 medicines")

    def test_edit_keeps_code_and_toggle(self):
        self.client.post(reverse("items:create"), medicine_form(self.tenant))
        with tenant_context(self.tenant):
            medicine = Medicine.objects.get()
        self.client.post(reverse("items:edit", args=[medicine.pk]), medicine_form(self.tenant, code=medicine.code, sale_price="1.50"))
        medicine.refresh_from_db()
        self.assertEqual((medicine.code, medicine.sale_price), ("000001", Decimal("1.50")))
        self.client.post(reverse("items:toggle", args=[medicine.pk]))
        medicine.refresh_from_db()
        self.assertFalse(medicine.is_active)

    def test_staff_need_permission(self):
        self.client.force_login(add_staff(self.tenant, "c@x.test", ["item.list"]))
        self.assertEqual(self.client.get(reverse("items:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("items:create")).status_code, 403)


class SearchAndListTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            bulk_create_medicines(self.tenant, [
                {"name": "Napa", "strength": "500 mg", "dosage_form": "tablet", "generic": "Paracetamol", "manufacturer": "Beximco", "barcode": "8941100500101"},
                {"name": "Ace", "strength": "500 mg", "dosage_form": "tablet", "generic": "Paracetamol", "manufacturer": "Square"},
                {"name": "Seclo", "strength": "20 mg", "dosage_form": "capsule", "generic": "Omeprazole", "manufacturer": "Square"},
            ])
        other, _ = make_pharmacy("Green Life")
        with tenant_context(other):
            bulk_create_medicines(other, [{"name": "Napa", "generic": "Paracetamol"}])

    def _data(self, **params):
        base = {"draw": 1, "start": 0, "length": 25}
        base.update(params)
        return self.client.get(reverse("items:data"), base).json()

    def test_list_is_scoped_and_searchable(self):
        self.assertEqual(self._data()["recordsTotal"], 3)
        self.assertEqual(self._data(**{"search[value]": "paracet 500"})["recordsFiltered"], 2)
        self.assertEqual(self._data(**{"search[value]": "8941100500101"})["recordsFiltered"], 1)
        self.assertEqual(self._data(manufacturer="Square")["recordsTotal"], 2)

    def test_lookups(self):
        generics = self.client.get(reverse("items:lookup", args=["generics"]), {"q": "ome"}).json()
        self.assertEqual(generics["results"], [{"id": "Omeprazole", "text": "Omeprazole"}])
        medicines = self.client.get(reverse("items:lookup", args=["medicines"]), {"q": "napa"}).json()["results"]
        self.assertEqual([m["text"] for m in medicines], ["Napa 500 mg Tablet"])

    def test_export_then_reimport_updates_prices(self):
        response = self.client.get(reverse("items:export"))
        sheet = load_workbook(BytesIO(response.content)).active
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        self.assertEqual(rows[0][:2], ["Code", "Name"])
        self.assertEqual(len(rows), 4)
        price_col = rows[0].index("Sale price")
        for row in rows[1:]:
            row[price_col] = 5
        upload = xlsx_upload(rows[0], rows[1:])
        with tenant_context(self.tenant):
            preview = parse_upload(upload, update_existing=True)
            self.assertEqual((len(preview.to_update), len(preview.to_create), preview.errors), (3, 0, []))
            run_import(self.tenant, preview.to_create, preview.to_update)
            self.assertEqual(set(Medicine.objects.values_list("sale_price", flat=True)), {Decimal("5.00")})


@override_settings(MEDIA_ROOT=MEDIA)
class ImportTests(TestCase):
    HEADERS = ["Brand", "Strength", "Form", "Generic name", "Company", "Unit", "Purchase unit", "Conversion", "TP", "MRP", "Barcode", "Rx"]

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_parse_understands_aliases_and_reports_problems(self):
        upload = xlsx_upload(self.HEADERS, [
            ["Napa", "500 mg", "Tab", "Paracetamol", "Beximco", "Piece", "Box", 200, 0.95, 1.2, "111", "no"],
            ["Seclo", "20 mg", "Cap", "Omeprazole", "Square", "Piece", "Box", 100, 4.5, 6, "", "yes"],
            ["", "1 mg", "Tab", "", "", "", "", "", "", "", "", ""],
            ["Bad", "", "", "", "", "", "", "ten", "", "", "", ""],
            ["Dupe", "", "", "", "", "", "", "", "", "", "111", ""],
        ])
        with tenant_context(self.tenant):
            preview = parse_upload(upload)
        self.assertEqual(len(preview.to_create), 2)
        self.assertEqual([n for n, _ in preview.errors], [4, 5, 6])
        napa = preview.to_create[0]
        self.assertEqual((napa["dosage_form"], napa["conversion"], napa["sale_price"]), ("tablet", 200, Decimal("1.2")))
        self.assertTrue(preview.to_create[1]["prescription_required"])

    def test_upload_preview_confirm(self):
        upload = xlsx_upload(self.HEADERS, [["Napa", "500 mg", "Tab", "Paracetamol", "Beximco", "Piece", "Box", 200, 0.95, 1.2, "", ""]])
        self.assertRedirects(self.client.post(reverse("items:import"), {"file": upload}), reverse("items:import_preview"))
        self.assertContains(self.client.get(reverse("items:import_preview")), "Import now (1 new, 0 updates)")
        response = self.client.post(reverse("items:import_preview"), {"confirm": "1"})
        self.assertRedirects(response, reverse("items:list"))
        with tenant_context(self.tenant):
            napa = Medicine.objects.get()
            self.assertEqual((napa.purchase_unit.name, napa.conversion, napa.manufacturer.name), ("Box", 200, "Beximco"))
        self.assertNotIn("item_import", self.client.session)

    def test_csv_and_missing_name_column(self):
        csv = SimpleUploadedFile("m.csv", "Name,MRP\nNapa,1.20\nAce,1.10\n".encode(), content_type="text/csv")
        with tenant_context(self.tenant):
            self.assertEqual(len(parse_upload(csv).to_create), 2)
        bad = SimpleUploadedFile("m.csv", b"Price\n1\n", content_type="text/csv")
        response = self.client.post(reverse("items:import"), {"file": bad})
        self.assertContains(response, "No “Name” column found")

    def test_existing_code_without_update_is_an_error(self):
        with tenant_context(self.tenant):
            bulk_create_medicines(self.tenant, [{"name": "Napa", "code": "N1"}])
            preview = parse_upload(xlsx_upload(["Code", "Name"], [["N1", "Napa 2"]]))
        self.assertIn("already used", preview.errors[0][1])

    def test_import_respects_plan_limit(self):
        self.tenant.plan = Plan.objects.create(name="Tiny", slug="tiny", max_medicines=1)
        self.tenant.save()
        upload = xlsx_upload(["Name"], [["A"], ["B"]])
        self.client.post(reverse("items:import"), {"file": upload})
        response = self.client.post(reverse("items:import_preview"), {"confirm": "1"}, follow=True)
        self.assertContains(response, "plan allows 1 medicines")
        with tenant_context(self.tenant):
            self.assertFalse(Medicine.objects.exists())


class BulkPriceTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            bulk_create_medicines(self.tenant, [
                {"name": "Napa", "manufacturer": "Beximco", "purchase_price": Decimal("1.00"), "sale_price": Decimal("1.20")},
                {"name": "Ace", "manufacturer": "Square", "purchase_price": Decimal("1.00"), "sale_price": Decimal("1.10")},
            ])

    def test_new_price_rules(self):
        m = Medicine(purchase_price=Decimal("10"), sale_price=Decimal("12.34"))
        self.assertEqual(new_price(m, "sale_price", "increase", 10, "cents"), Decimal("13.58"))  # 13.574 rounded up
        self.assertEqual(new_price(m, "sale_price", "increase", 10, "whole"), Decimal("14"))
        self.assertEqual(new_price(m, "sale_price", "decrease", 10, "half"), Decimal("11"))  # 11.106 rounded down
        self.assertEqual(new_price(m, "sale_price", "margin", 25, "cents"), Decimal("12.50"))
        self.assertIsNone(new_price(Medicine(purchase_price=0), "sale_price", "margin", 25, "cents"))

    def test_an_increase_never_lowers_a_price(self):
        cheap = Medicine(sale_price=Decimal("1.04"))
        self.assertEqual(new_price(cheap, "sale_price", "increase", 10, "half"), Decimal("1.5"))
        self.assertEqual(new_price(cheap, "sale_price", "decrease", 10, "half"), Decimal("0.5"))

    def test_preview_then_apply_to_filtered_medicines(self):
        params = {"manufacturer": "Square", "field": "sale_price", "method": "increase", "percent": "10", "rounding": "cents"}
        response = self.client.get(reverse("items:bulk_price"), params)
        self.assertEqual(response.context["count"], 1)
        self.client.post(reverse("items:bulk_price"), params)
        with tenant_context(self.tenant):
            prices = dict(Medicine.objects.values_list("name", "sale_price"))
        self.assertEqual(prices, {"Napa": Decimal("1.20"), "Ace": Decimal("1.21")})


class SetupListTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_defaults_crud_and_duplicates(self):
        data = self.client.get(reverse("items:units_data"), {"draw": 1, "start": 0, "length": 50}).json()
        self.assertEqual(data["recordsTotal"], 10)
        self.assertContains(self.client.post(reverse("items:categories_create"), {"name": "Medicine"}), "already exists")
        self.assertRedirects(self.client.post(reverse("items:racks_create"), {"name": "A-1"}), reverse("items:racks"))
        with tenant_context(self.tenant):
            a1 = Rack.objects.get(name="A-1")
            self.assertTrue(Category.objects.filter(name="Medicine").exists())
        self.client.post(reverse("items:racks_toggle", args=[a1.pk]))
        a1.refresh_from_db()
        self.assertFalse(a1.is_active)

    def test_unknown_kind_is_404_and_labels_page_loads(self):
        self.assertEqual(self.client.get(reverse("items:lookup", args=["secrets"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("items:labels")).status_code, 200)

    def test_manufacturer_names_match_case_insensitively(self):
        with tenant_context(self.tenant):
            bulk_create_medicines(self.tenant, [{"name": "A", "manufacturer": "Square"}, {"name": "B", "manufacturer": "SQUARE"}])
            self.assertEqual(Manufacturer.objects.count(), 1)
