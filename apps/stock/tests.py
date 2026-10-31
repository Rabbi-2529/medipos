import shutil
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.core.dates import parse_expiry
from apps.core.spreadsheets import XLSX
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.outlets.models import Outlet

from . import opening
from .models import Batch, BatchStock, StockMovement
from .services import StockError, available_batches, format_quantity, get_or_create_batch, on_hand, post_movement

MEDIA = tempfile.mkdtemp()
KIND = StockMovement.Kind


class ExpiryParsingTests(TestCase):
    def test_formats(self):
        self.assertEqual(parse_expiry("2027-02"), date(2027, 2, 28))
        self.assertEqual(parse_expiry("02/2028"), date(2028, 2, 29))
        self.assertEqual(parse_expiry("2/27"), date(2027, 2, 28))
        self.assertEqual(parse_expiry("Feb-27"), date(2027, 2, 28))
        self.assertEqual(parse_expiry("15/03/2027"), date(2027, 3, 15))
        self.assertIsNone(parse_expiry(""))
        with self.assertRaises(ValueError):
            parse_expiry("soon")


class StockServiceTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant)
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def test_in_and_out_with_running_balance(self):
        batch = get_or_create_batch(self.napa, "B1", date(2027, 6, 30), Decimal("0.90"))
        post_movement(outlet=self.outlet, batch=batch, quantity=100, kind=KIND.PURCHASE, ref=("purchase", 1, "PUR-1"))
        out = post_movement(outlet=self.outlet, batch=batch, quantity=-30, kind=KIND.SALE)
        self.assertEqual((out.balance_after, out.unit_cost), (70, Decimal("0.9000")))
        self.assertEqual(on_hand(self.outlet, self.napa), 70)
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_never_negative_and_nothing_changes_on_refusal(self):
        batch = get_or_create_batch(self.napa, "B1", date(2027, 6, 30))
        post_movement(outlet=self.outlet, batch=batch, quantity=5, kind=KIND.OPENING)
        with self.assertRaisesMessage(StockError, "5 left, 6 needed"):
            post_movement(outlet=self.outlet, batch=batch, quantity=-6, kind=KIND.SALE)
        self.assertEqual(on_hand(self.outlet, self.napa), 5)
        self.assertEqual(StockMovement.objects.count(), 1)
        with self.assertRaises(StockError):
            post_movement(outlet=self.outlet, batch=batch, quantity=0, kind=KIND.SALE)

    def test_batches_are_reused_and_refreshed(self):
        first = get_or_create_batch(self.napa, " B1 ", date(2027, 6, 30), Decimal("1"))
        again = get_or_create_batch(self.napa, "B1", date(2027, 6, 30), Decimal("1.10"))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.unit_cost, Decimal("1.10"))
        no_expiry = get_or_create_batch(self.napa, "", None)
        self.assertEqual(get_or_create_batch(self.napa, "", None).pk, no_expiry.pk)
        self.assertEqual(Batch.objects.count(), 2)

    def test_fefo_order(self):
        late = get_or_create_batch(self.napa, "L", date(2028, 1, 31))
        soon = get_or_create_batch(self.napa, "S", date(2027, 1, 31))
        for batch in (late, soon):
            post_movement(outlet=self.outlet, batch=batch, quantity=10, kind=KIND.OPENING)
        self.assertEqual([s.batch.batch_no for s in available_batches(self.outlet, self.napa)], ["S", "L"])

    def test_format_quantity(self):
        self.assertEqual(format_quantity(25, self.napa), "2 Box + 5 Piece")
        self.assertEqual(format_quantity(20, self.napa), "2 Box")
        self.assertEqual(format_quantity(7, self.napa), "7 Piece")


class StockScreenTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant)
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()
            self.gulshan = Outlet.objects.create(name="Gulshan", code="0002")
            batch = get_or_create_batch(self.napa, "B1", date(2027, 6, 30), Decimal("0.90"))
            post_movement(outlet=self.main, batch=batch, quantity=100, kind=KIND.OPENING)
            post_movement(outlet=self.gulshan, batch=batch, quantity=40, kind=KIND.OPENING)
        other, _ = make_pharmacy("Green Life")
        make_medicine(other, "Napa")

    def _data(self, **params):
        base = {"draw": 1, "start": 0, "length": 25}
        base.update(params)
        return self.client.get(reverse("stock:data"), base).json()

    def test_owner_sees_each_or_all_outlets(self):
        self.client.force_login(self.owner)
        self.assertIn("14 Box", str(self._data()["data"]))  # all outlets: 140 pieces
        main = self._data(outlet=self.main.pk)
        self.assertIn("10 Box", str(main["data"]))
        self.assertEqual(main["summary"]["cost"], "৳90.00")
        self.assertEqual(self._data()["recordsTotal"], 1)  # other pharmacy's Napa never shows

    def test_staff_only_see_their_outlets(self):
        staff = add_staff(self.tenant, "s@x.test", ["stock.list"], outlets=[self.gulshan])
        self.client.force_login(staff)
        self.assertIn("4 Box", str(self._data(outlet=self.main.pk)["data"]))  # asking for Main still gives Gulshan
        card = self.client.get(reverse("stock:card", args=[self.napa.pk]))
        self.assertEqual([s.outlet for s in card.context["stocks"]], [self.gulshan])
        moves = self.client.get(reverse("stock:movements", args=[self.napa.pk]), {"draw": 1, "start": 0, "length": 10}).json()
        self.assertEqual(moves["recordsTotal"], 1)


def workbook(headers, rows):
    wb = Workbook()
    wb.active.append(headers)
    for row in rows:
        wb.active.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return SimpleUploadedFile("opening.xlsx", buffer.getvalue(), content_type=XLSX)


@override_settings(MEDIA_ROOT=MEDIA)
class OpeningStockTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", barcode="111")
        self.syrup = make_medicine(self.tenant, "Napa Syrup", conversion=1, track_expiry=True, code="SYR")
        self.cotton = make_medicine(self.tenant, "Cotton", conversion=1, track_expiry=False, code="COT")
        with tenant_context(self.tenant):
            self.outlet = Outlet.objects.get()
        self.client.force_login(self.owner)

    def test_template_lists_medicines(self):
        response = self.client.get(reverse("stock:opening_template"))
        rows = list(load_workbook(BytesIO(response.content)).active.iter_rows(values_only=True))
        self.assertEqual(rows[0][:3], ("Code", "Barcode", "Name"))
        self.assertEqual(len(rows), 4)

    def test_parse_matches_and_reports(self):
        upload = workbook(["Code", "Barcode", "Name", "Batch", "Expiry", "Packs", "Quantity", "Unit cost"], [
            ["", "111", "", "B1", "2027-06", 2, 5, ""],          # barcode match: 2 boxes + 5 = 25
            ["SYR", "", "", "S1", "", "", 3, 40],                  # expiry missing
            ["", "", "Cotton", "", "", "", 10, 5],                 # name match, no expiry needed
            ["NOPE", "", "", "", "", "", 1, ""],                   # unknown
            ["COT", "", "", "", "", "", 0, ""],                    # nothing on shelf -> skipped silently
        ])
        with tenant_context(self.tenant):
            rows, errors, total = opening.parse(upload)
        self.assertEqual(total, 5)
        self.assertEqual([(r["name"], r["quantity"], r["unit_cost"]) for r in rows], [("Napa", 25, "1.0000"), ("Cotton", 10, "5")])
        self.assertEqual([n for n, _ in errors], [3, 5])

    def test_upload_preview_confirm(self):
        upload = workbook(["Code", "Batch", "Expiry", "Quantity"], [["NAPA", "B1", "06/2027", 30]])
        response = self.client.post(reverse("stock:opening"), {"outlet": self.outlet.pk, "file": upload})
        self.assertRedirects(response, reverse("stock:opening_preview"))
        self.assertContains(self.client.get(reverse("stock:opening_preview")), "Add 1 batches to Main branch")
        self.client.post(reverse("stock:opening_preview"), {"confirm": "1"})
        with tenant_context(self.tenant):
            self.assertEqual(on_hand(self.outlet, self.napa), 30)
            movement = StockMovement.objects.get()
            self.assertEqual((movement.kind, movement.created_by), (KIND.OPENING, self.owner))
            self.assertEqual(BatchStock.objects.get().batch.expiry_date, date(2027, 6, 30))
