import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.outlets.models import Outlet
from apps.stock.models import Batch, StockMovement
from apps.stock.services import batch_on_hand, get_or_create_batch, on_hand, post_movement

from .models import Transfer, WriteOff
from .services import (
    InventoryError, cancel_transfer, clean_count_lines, clean_out_lines, create_write_off, dispatch_transfer, receive_transfer,
    save_stock_count, write_off_expired,
)

TODAY = timezone.localdate()


class InventorySetup(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.napa = make_medicine(self.tenant, "Napa", conversion=100, sale_price="1.20", alert_quantity=50)
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.main = Outlet.objects.get()
        self.gulshan = Outlet.objects.create(name="Gulshan", code="GUL")
        self.good = self._stock("GOOD", TODAY + timedelta(days=300), 100, "1.00")
        self.old = self._stock("OLD", TODAY - timedelta(days=3), 20, "0.80")

    def _stock(self, batch_no, expiry, quantity, cost, outlet=None):
        batch = get_or_create_batch(self.napa, batch_no, expiry, Decimal(cost))
        post_movement(outlet=outlet or self.main, batch=batch, quantity=quantity, kind=StockMovement.Kind.OPENING)
        return batch


class WriteOffTests(InventorySetup):
    def test_write_off_takes_stock_and_values_the_loss(self):
        lines = clean_out_lines(self.main, [{"batch": self.good.pk, "qty": "10"}, {"batch": self.good.pk, "qty": 5}])
        write_off = create_write_off(tenant=self.tenant, outlet=self.main, date=TODAY, reason=WriteOff.Reason.DAMAGED, lines=lines)
        self.assertEqual((write_off.number, write_off.total_cost, batch_on_hand(self.main, self.good)), ("DMG-00001", Decimal("15.00"), 85))
        self.assertEqual(StockMovement.objects.get(ref_type="write_off").kind, StockMovement.Kind.DAMAGE)

    def test_cannot_write_off_more_than_on_hand(self):
        with self.assertRaisesMessage(InventoryError, "only 20 at Main"):
            clean_out_lines(self.main, [{"batch": self.old.pk, "qty": 21}])
        with self.assertRaises(InventoryError):
            clean_out_lines(self.main, [])

    def test_write_off_all_expired(self):
        write_off = write_off_expired(tenant=self.tenant, outlet=self.main)
        self.assertEqual((write_off.reason, write_off.total_cost), (WriteOff.Reason.EXPIRED, Decimal("16.00")))
        self.assertEqual((batch_on_hand(self.main, self.old), batch_on_hand(self.main, self.good)), (0, 100))
        with self.assertRaisesMessage(InventoryError, "no expired stock"):
            write_off_expired(tenant=self.tenant, outlet=self.main)


class TransferTests(InventorySetup):
    def test_dispatch_then_receive_balances_both_sides(self):
        transfer = dispatch_transfer(tenant=self.tenant, from_outlet=self.main, to_outlet=self.gulshan,
                                     lines=clean_out_lines(self.main, [{"batch": self.good.pk, "qty": 30}]))
        self.assertEqual((transfer.number, transfer.status, transfer.total_cost), ("TRF-00001", Transfer.Status.IN_TRANSIT, Decimal("30.00")))
        self.assertEqual((on_hand(self.main, self.napa), on_hand(self.gulshan, self.napa)), (90, 0))  # in transit
        receive_transfer(transfer)
        self.assertEqual((batch_on_hand(self.main, self.good), batch_on_hand(self.gulshan, self.good)), (70, 30))
        moves = StockMovement.objects.filter(ref_type="transfer")
        self.assertEqual(sum(m.quantity for m in moves), 0)  # out and in cancel each other
        with self.assertRaisesMessage(InventoryError, "already received"):
            cancel_transfer(transfer)

    def test_cancel_returns_stock_and_same_outlet_is_refused(self):
        transfer = dispatch_transfer(tenant=self.tenant, from_outlet=self.main, to_outlet=self.gulshan,
                                     lines=[(self.good, 10)])
        cancel_transfer(transfer)
        self.assertEqual((batch_on_hand(self.main, self.good), batch_on_hand(self.gulshan, self.good)), (100, 0))
        with self.assertRaises(InventoryError):
            dispatch_transfer(tenant=self.tenant, from_outlet=self.main, to_outlet=self.main, lines=[(self.good, 1)])


class StockCountTests(InventorySetup):
    def test_count_moves_stock_by_the_difference(self):
        lines = clean_count_lines(self.main, [{"batch": self.good.pk, "counted": "96"}, {"batch": self.old.pk, "counted": ""}],
                                  [{"medicine": self.napa.pk, "batch_no": "FOUND", "expiry": (TODAY + timedelta(days=200)).strftime("%Y-%m"), "counted": 7}])
        count = save_stock_count(tenant=self.tenant, outlet=self.main, date=TODAY, lines=lines, note="Rack A")
        found = Batch.objects.get(batch_no="FOUND")
        self.assertEqual((batch_on_hand(self.main, self.good), batch_on_hand(self.main, self.old), batch_on_hand(self.main, found)), (96, 20, 7))
        self.assertEqual((count.number, count.loss_cost, count.gain_cost), ("ADJ-00001", Decimal("4.00"), Decimal("0.07")))
        self.assertEqual(count.lines.count(), 2)

    def test_found_batch_needs_an_expiry_when_tracked(self):
        with self.assertRaisesMessage(InventoryError, "enter the expiry"):
            clean_count_lines(self.main, [], [{"medicine": self.napa.pk, "batch_no": "X", "expiry": "", "counted": 3}])


class InventoryViewTests(InventorySetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_batches_endpoint_and_write_off_form(self):
        data = self.client.get(reverse("inventory:batches"), {"outlet": self.main.pk, "medicine": self.napa.pk}).json()
        self.assertEqual([(b["batchNo"], b["expired"], b["onHand"]) for b in data["batches"]], [("OLD", True, 20), ("GOOD", False, 100)])
        response = self.client.post(reverse("inventory:damage_create"), {
            "outlet": self.main.pk, "date": TODAY.isoformat(), "reason": "damaged", "note": "Dropped",
            "lines": json.dumps([{"medicine": self.napa.pk, "batch": self.good.pk, "qty": 4}]),
        })
        write_off = WriteOff.objects.get()
        self.assertRedirects(response, reverse("inventory:damage", args=[write_off.pk]))
        self.assertContains(self.client.get(reverse("inventory:damage", args=[write_off.pk])), "Dropped")
        # Arriving from the expiring-stock report pre-fills the batch.
        page = self.client.get(reverse("inventory:damage_create"), {"outlet": self.main.pk, "batch": self.old.pk, "reason": "expired"})
        self.assertEqual(page.context["initial_lines"][0]["qty"], 20)

    def test_only_the_receiving_outlet_can_receive(self):
        transfer = dispatch_transfer(tenant=self.tenant, from_outlet=self.main, to_outlet=self.gulshan, lines=[(self.good, 5)])
        main_staff = add_staff(self.tenant, "m@x.test", ["transfer.list", "transfer.create"], outlets=[self.main])
        self.client.force_login(main_staff)
        self.assertEqual(self.client.post(reverse("inventory:transfer_receive", args=[transfer.pk])).status_code, 404)
        self.assertFalse(self.client.get(reverse("inventory:transfer", args=[transfer.pk])).context["can_receive"])
        gulshan_staff = add_staff(self.tenant, "g@x.test", ["transfer.list", "transfer.create"], outlets=[self.gulshan])
        self.client.force_login(gulshan_staff)
        self.client.post(reverse("inventory:transfer_receive", args=[transfer.pk]))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.Status.RECEIVED)

    def test_reports(self):
        low = self.client.get(reverse("stock:low_data"), {"draw": 1, "start": 0, "length": 10, "outlet": self.gulshan.pk}).json()
        self.assertEqual((low["recordsTotal"], low["summary"]), (1, {"low": 0, "out": 1}))  # alert 50, nothing at Gulshan
        expiring = self.client.get(reverse("stock:expiring_data"), {"draw": 1, "start": 0, "length": 10, "outlet": self.main.pk, "days": "0"}).json()
        self.assertEqual((expiring["recordsTotal"], expiring["summary"]["expiredCount"]), (1, 1))
        self.assertEqual(self.client.get(reverse("stock:expiring_export"), {"days": "90"}).status_code, 200)
        self.client.post(reverse("inventory:damage_expired"), {"outlet": self.main.pk})
        self.assertEqual(batch_on_hand(self.main, self.old), 0)
        self.assertContains(self.client.get(reverse("core:dashboard")), "Expired, still on the shelf")
