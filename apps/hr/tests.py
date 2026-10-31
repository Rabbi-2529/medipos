from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounting import posting, reports
from apps.accounting.chart import money_account, system_account
from apps.accounting.models import JournalEntry, PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.outlets.models import Outlet
from apps.people.models import Employee
from apps.sales.services import complete_sale, start_register
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from .models import Attendance, OfficeNetwork, Payroll, Schedule, Shift
from .services import (
    HRError, absent_days, active_staff, attendance_counts, check_in, check_out, clean_network, client_ip, delete_advance, delete_draft,
    give_advance, on_office_network, outstanding_advance, pay_payroll, prepare_payroll, recalculate, save_attendance, update_lines,
)

TODAY = timezone.localdate()
SEPTEMBER = date(2026, 9, 1)  # 30 days


class PayrollTests(TestCase):
    """Attendance feeds absences, POS sales feed commission, advances come back, and the books balance."""

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.outlet = Outlet.objects.get()
        self.cash = PaymentAccount.objects.get(name="Cash")
        cashier = add_staff(self.tenant, "cashier@x.test", ["pos.create"], all_outlets=True)
        self.cashier = Employee.objects.get(user=cashier)
        self.cashier.salary, self.cashier.allowance, self.cashier.commission_percent = Decimal("15000"), Decimal("1000"), Decimal("1")
        self.cashier.save()
        self.rider = Employee.objects.create(name="Karim Mia", designation="Delivery rider", salary=Decimal("9000"))
        napa = make_medicine(self.tenant, "Napa", conversion=10, purchase_price="8.00", sale_price="100.00")
        batch = get_or_create_batch(napa, "B1", TODAY + timedelta(days=400), Decimal("8"))
        post_movement(outlet=self.outlet, batch=batch, quantity=500, kind=StockMovement.Kind.OPENING)
        register = start_register(cashier, self.outlet, "0")
        complete_sale(tenant=self.tenant, outlet=self.outlet, user=cashier, register=register, lines=[{"medicine": napa.pk, "qty": 50}],
                      payments=[{"account": self.cash.pk, "amount": 5000}], now=timezone.make_aware(datetime(2026, 9, 10, 12)))

    def test_staff_without_login_is_on_staff_and_the_owner_is_not(self):
        staff = active_staff()
        self.assertIn(self.rider, staff)
        self.assertIn(self.cashier, staff)
        self.assertFalse(any(e.user_id == self.owner.pk for e in staff))
        self.assertFalse(self.rider.has_login)
        self.assertEqual(self.rider.display_name, "Karim Mia")

    def test_attendance_counts_half_days_as_half(self):
        save_attendance(day=date(2026, 9, 2), rows=[{"employee": self.rider, "status": "absent"}, {"employee": self.cashier, "status": "present"}])
        save_attendance(day=date(2026, 9, 3), rows=[{"employee": self.rider, "status": "half"}])
        save_attendance(day=date(2026, 9, 3), rows=[{"employee": self.rider, "status": "half", "note": "Doctor"}])  # saving again updates
        counts = attendance_counts([self.rider.pk], SEPTEMBER, date(2026, 9, 30))[self.rider.pk]
        self.assertEqual(absent_days(counts), Decimal("1.5"))
        self.assertEqual(Attendance.objects.filter(employee=self.rider).count(), 2)
        save_attendance(day=date(2026, 9, 3), rows=[{"employee": self.rider, "status": ""}])  # cleared
        self.assertEqual(Attendance.objects.filter(employee=self.rider).count(), 1)
        with self.assertRaises(HRError):
            save_attendance(day=date(2026, 9, 4), rows=[{"employee": self.rider, "status": "holiday"}])

    def test_prepare_pay_and_books(self):
        save_attendance(day=date(2026, 9, 2), rows=[{"employee": self.rider, "status": "absent"}])
        save_attendance(day=date(2026, 9, 3), rows=[{"employee": self.rider, "status": "half"}])
        give_advance(tenant=self.tenant, employee=self.cashier, date=date(2026, 9, 5), amount=Decimal("2000"), payment_account=self.cash)
        self.assertEqual(outstanding_advance(self.cashier), Decimal("2000"))

        payroll = prepare_payroll(tenant=self.tenant, month=date(2026, 9, 17))
        self.assertEqual(payroll.month, SEPTEMBER)
        lines = {line.employee_id: line for line in payroll.lines.all()}
        self.assertEqual(set(lines), {self.cashier.pk, self.rider.pk})
        rider, cashier = lines[self.rider.pk], lines[self.cashier.pk]
        self.assertEqual(rider.absent_days, Decimal("1.5"))
        self.assertEqual(rider.absence_deduction, Decimal("450.00"))  # 9000 / 30 × 1.5
        self.assertEqual(rider.net, Decimal("8550.00"))
        self.assertEqual(cashier.sales, Decimal("5000.00"))
        self.assertEqual(cashier.commission, Decimal("50.00"))
        self.assertEqual(cashier.advance_deduction, Decimal("2000"))
        self.assertEqual(cashier.net, Decimal("14050.00"))  # 15000 + 1000 + 50 − 2000
        with self.assertRaises(HRError):
            prepare_payroll(tenant=self.tenant, month=SEPTEMBER)  # one per month

        update_lines(payroll, {cashier.pk: {"bonus": "500", "other_deduction": "0", "absence_deduction": "0", "advance_deduction": "1000"}})
        with self.assertRaises(HRError):
            update_lines(payroll, {cashier.pk: {"advance_deduction": "2500", "absence_deduction": "0"}})  # owes only 2000
        with self.assertRaises(HRError):
            update_lines(payroll, {rider.pk: {"bonus": "-5"}})
        recalculate(payroll)  # refreshes the automatic parts, keeps the bonus
        cashier.refresh_from_db()
        self.assertEqual((cashier.bonus, cashier.advance_deduction), (Decimal("500"), Decimal("2000")))

        cash_before = reports.balance_of(money_account(self.cash))
        payroll = pay_payroll(payroll, account=self.cash, day=date(2026, 10, 1), user=self.owner)
        totals = payroll.totals()
        self.assertEqual(totals["gross"], Decimal("25100.00"))  # 16550 + 8550
        self.assertEqual(reports.balance_of(system_account("salaries")), totals["gross"])
        self.assertEqual(reports.balance_of(system_account("advances")), 0)
        self.assertEqual(cash_before - reports.balance_of(money_account(self.cash)), totals["net"])
        self.assertTrue(reports.trial_balance(date(2026, 10, 31))["balanced"])
        self.assertEqual(outstanding_advance(self.cashier), 0)

        with self.assertRaises(HRError):
            update_lines(payroll, {})
        with self.assertRaises(HRError):
            delete_draft(payroll)
        with self.assertRaises(HRError):
            delete_advance(self.cashier.advances.get())  # already taken back

        posting.rebuild()
        self.assertEqual(reports.balance_of(system_account("salaries")), totals["gross"])
        self.assertTrue(reports.trial_balance(date(2026, 10, 31))["balanced"])

    def test_an_advance_bigger_than_the_pay_carries_over(self):
        give_advance(tenant=self.tenant, employee=self.rider, date=date(2026, 9, 5), amount=Decimal("12000"), payment_account=self.cash)
        payroll = prepare_payroll(tenant=self.tenant, month=SEPTEMBER)
        line = payroll.lines.get(employee=self.rider)
        self.assertEqual((line.advance_deduction, line.net), (Decimal("9000"), Decimal("0")))
        pay_payroll(payroll, account=self.cash, day=date(2026, 10, 1))
        self.assertEqual(outstanding_advance(self.rider), Decimal("3000"))

    def test_unused_advance_and_draft_can_be_deleted(self):
        advance = give_advance(tenant=self.tenant, employee=self.rider, date=date(2026, 9, 5), amount=Decimal("500"), payment_account=self.cash)
        self.assertTrue(JournalEntry.objects.filter(source_type="advance", source_id=advance.pk).exists())
        delete_advance(advance)
        self.assertFalse(JournalEntry.objects.filter(source_type="advance").exists())
        payroll = prepare_payroll(tenant=self.tenant, month=SEPTEMBER)
        delete_draft(payroll)
        self.assertFalse(Payroll.objects.exists())
        with self.assertRaises(HRError):
            give_advance(tenant=self.tenant, employee=self.rider, date=TODAY, amount=Decimal("0"), payment_account=self.cash)


class HRPageTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.cash = PaymentAccount.objects.get(name="Cash")
            self.rider = Employee.objects.create(name="Karim Mia", designation="Delivery rider", salary=Decimal("9000"))

    def test_staff_without_login_from_the_employee_form(self):
        response = self.client.post(reverse("people:employee_create"), {
            "first_name": "Selim", "last_name": "Uddin", "phone": "01700000001", "designation": "Cleaner", "email": "",
            "password1": "", "password2": "", "role": "", "joined_on": "", "nid": "", "address": "",
            "salary": "7000", "allowance": "0", "commission_percent": "0",
        })
        self.assertRedirects(response, reverse("people:employees"))
        with tenant_context(self.tenant):
            selim = Employee.objects.get(name="Selim Uddin")
        self.assertIsNone(selim.user_id)
        self.assertEqual(selim.salary, 7000)

    def test_attendance_sheet_calendar_and_events(self):
        day = "2026-09-02"
        response = self.client.post(reverse("hr:attendance"), {"date": day, f"status-{self.rider.pk}": "late", f"in-{self.rider.pk}": "09:40",
                                                              f"note-{self.rider.pk}": "Traffic"})
        self.assertRedirects(response, f"{reverse('hr:attendance')}?date={day}")
        with tenant_context(self.tenant):
            mark = Attendance.objects.get(employee=self.rider)
        self.assertEqual((mark.status, mark.check_in.strftime("%H:%M"), mark.note), ("late", "09:40", "Traffic"))
        page = self.client.get(reverse("hr:attendance"), {"date": day})
        self.assertContains(page, "Karim Mia")
        self.assertContains(page, 'value="late" checked')
        self.assertEqual(self.client.get(reverse("hr:attendance_calendar")).status_code, 200)
        everyone = self.client.get(reverse("hr:attendance_events"), {"start": "2026-08-30", "end": "2026-10-11"}).json()
        self.assertEqual([(e["start"], e["extendedProps"]["count"]) for e in everyone], [(day, 1)])
        one = self.client.get(reverse("hr:attendance_events"), {"start": "2026-08-30", "end": "2026-10-11", "employee": self.rider.pk}).json()
        self.assertEqual(one[0]["extendedProps"]["times"], "09:40")

    def test_advance_payroll_pay_and_payslip(self):
        response = self.client.post(reverse("hr:advances"), {"employee": self.rider.pk, "amount": "1000", "date": "2026-09-05",
                                                             "payment_account": self.cash.pk, "note": "Eid"})
        self.assertRedirects(response, reverse("hr:advances"))
        self.assertContains(self.client.get(reverse("hr:advances"), {"start": "2026-09-01", "end": "2026-09-30"}), "Eid")
        response = self.client.post(reverse("hr:payroll"), {"month": "2026-09"})
        with tenant_context(self.tenant):
            payroll = Payroll.objects.get()
            line = payroll.lines.get()
        self.assertRedirects(response, reverse("hr:payroll_detail", args=[payroll.pk]))
        self.assertContains(self.client.get(reverse("hr:payroll_detail", args=[payroll.pk])), "Karim Mia")
        self.client.post(reverse("hr:payroll_detail", args=[payroll.pk]), {
            f"bonus-{line.pk}": "300", f"absence_deduction-{line.pk}": "0", f"other_deduction-{line.pk}": "0",
            f"advance_deduction-{line.pk}": "1000", f"note-{line.pk}": "Good month"})
        response = self.client.post(reverse("hr:payroll_pay", args=[payroll.pk]), {"payment_account": self.cash.pk, "date": "2026-10-01"}, follow=True)
        self.assertContains(response, "paid")
        with tenant_context(self.tenant):
            payroll.refresh_from_db()
            self.assertEqual(payroll.totals()["net"], Decimal("8300.00"))  # 9000 + 300 − 1000
        self.assertEqual(payroll.status, Payroll.Status.PAID)
        slip = self.client.get(reverse("hr:payslip", args=[payroll.pk, line.pk]))
        self.assertContains(slip, "Karim Mia")
        self.assertContains(slip, "৳8,300.00")
        self.assertContains(self.client.get(reverse("hr:payroll")), payroll.number)

    def test_permissions(self):
        cashier = add_staff(self.tenant, "c@x.test", ["pos.create"])
        self.client.force_login(cashier)
        for name in ("attendance", "attendance_calendar", "advances", "payroll"):
            self.assertEqual(self.client.get(reverse(f"hr:{name}")).status_code, 403, name)
        viewer = add_staff(self.tenant, "v@x.test", ["attendance.list", "salary.list"])
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("hr:attendance")).status_code, 200)
        self.assertEqual(self.client.post(reverse("hr:attendance"), {f"status-{self.rider.pk}": "present"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("hr:payroll"), {"month": "2026-09"}).status_code, 404)
        # Pay fields on the employee form need salary.create.
        self.client.force_login(add_staff(self.tenant, "e@x.test", ["employee.list", "employee.edit"]))
        self.assertNotContains(self.client.get(reverse("people:employee_edit", args=[self.rider.pk])), 'name="salary"')


class SelfCheckTests(TestCase):
    """Shifts decide late and leaving early; away from the shop's networks a reason is needed to check in or out."""

    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.user = add_staff(self.tenant, "cashier@x.test", ["pos.create"], all_outlets=True)
        with tenant_context(self.tenant):
            self.morning = Shift.objects.create(name="Morning", start=time(9, 0), end=time(17, 0), grace_minutes=10)
            self.me = Employee.objects.get(user=self.user)
            self.me.shifts.set([self.morning])

    def at(self, hour, minute, day=date(2026, 10, 5)):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def test_late_rule_handles_night_shifts(self):
        morning = Schedule([self.morning])
        self.assertFalse(morning.is_late(time(8, 50)))
        self.assertFalse(morning.is_late(time(9, 10)))
        self.assertTrue(morning.is_late(time(9, 11)))
        night = Schedule([Shift(name="Night", start=time(22, 0), end=time(6, 0), grace_minutes=15)])
        self.assertFalse(night.is_late(time(21, 45)))
        self.assertTrue(night.is_late(time(22, 30)))
        self.assertTrue(night.is_late(time(1, 0)))
        self.assertTrue(night.left_early(time(22, 0), time(5, 30)))
        self.assertFalse(night.left_early(time(21, 45), time(6, 0)))

    def test_several_shifts_make_one_day(self):
        def shift(name, start, end, grace=10):
            return Shift(name=name, start=time(*start), end=time(*end), grace_minutes=grace)

        day, evening = shift("Day", (9, 0), (17, 0)), shift("Evening", (15, 0), (23, 0), grace=30)
        both = Schedule([evening, day])
        self.assertEqual((both.names, both.start, both.end), ("Day + Evening", time(9, 0), time(23, 0)))
        self.assertTrue(both.is_late(time(9, 15)))  # the first shift's grace counts
        self.assertTrue(both.is_late(time(18, 0)))
        self.assertTrue(both.left_early(time(9, 0), time(17, 0)))  # the day shift is over, the evening isn't
        self.assertFalse(both.left_early(time(9, 0), time(23, 5)))
        self.assertTrue(both.left_early(None, time(22, 59)))
        self.assertFalse(Schedule([day]).left_early(time(9, 20), time(17, 0)))
        self.assertTrue(Schedule([day]).left_early(time(8, 50), time(8, 55)))  # gone before it even started

        split = Schedule([shift("Morning", (9, 0), (13, 0)), shift("Late", (17, 0), (21, 0))])  # a break in between
        self.assertEqual((split.start, split.end), (time(9, 0), time(21, 0)))
        self.assertTrue(split.left_early(time(9, 0), time(13, 0)))

        # Night into morning is one day from 22:00, whichever was entered first.
        overnight = Schedule([shift("Morning", (6, 0), (14, 0)), shift("Night", (22, 0), (6, 0))])
        self.assertEqual((overnight.names, overnight.start, overnight.end), ("Night + Morning", time(22, 0), time(14, 0)))
        self.assertFalse(overnight.is_late(time(21, 55)))
        self.assertTrue(overnight.left_early(time(21, 55), time(6, 30)))
        self.assertFalse(overnight.left_early(time(21, 55), time(14, 0)))

    def test_networks(self):
        with tenant_context(self.tenant):
            self.assertTrue(on_office_network("8.8.8.8"))  # nothing set: no check
            OfficeNetwork.objects.create(address=clean_network("103.4.5.0/24"))
            OfficeNetwork.objects.create(address=clean_network(" 2001:db8::1 "))
            self.assertTrue(on_office_network("103.4.5.77"))
            self.assertTrue(on_office_network("::ffff:103.4.5.77"))
            self.assertTrue(on_office_network("2001:db8::1"))
            self.assertFalse(on_office_network("8.8.8.8"))
            self.assertFalse(on_office_network(""))
            with self.assertRaises(HRError):
                clean_network("103.4.5")

    def test_check_in_and_out(self):
        with tenant_context(self.tenant):
            OfficeNetwork.objects.create(address="103.4.5.6")
            record = check_in(self.me, ip="103.4.5.6", now=self.at(9, 25))
            self.assertEqual((record.status, record.check_in, record.in_reason, record.self_marked), ("late", time(9, 25), "", True))
            with self.assertRaises(HRError):
                check_in(self.me, ip="103.4.5.6", now=self.at(9, 30))  # once a day
            with self.assertRaises(HRError):
                check_out(self.me, ip="8.8.8.8", now=self.at(17, 5))  # away and no reason
            record = check_out(self.me, ip="8.8.8.8", reason="Delivering to a customer", now=self.at(17, 5))
            self.assertEqual((record.check_out, record.out_reason, record.out_ip), (time(17, 5), "Delivering to a customer", "8.8.8.8"))
            self.assertFalse(record.left_early)
            with self.assertRaises(HRError):
                check_out(self.me, ip="103.4.5.6", now=self.at(17, 10))  # nothing open
            # A day the manager marked as leave turns into a working day when they come in.
            Attendance.objects.create(employee=self.me, date=date(2026, 10, 6), status="leave")
            self.assertEqual(check_in(self.me, ip="103.4.5.6", now=self.at(8, 55, date(2026, 10, 6))).status, "present")

            # On two shifts, leaving when the first one ends is early; after the last one it isn't.
            evening = Shift.objects.create(name="Evening", start=time(15, 0), end=time(23, 0))
            self.me = Employee.objects.get(pk=self.me.pk)
            self.me.shifts.add(evening)
            self.assertTrue(check_out(self.me, ip="103.4.5.6", now=self.at(17, 0, date(2026, 10, 6))).left_early)
            check_in(self.me, ip="103.4.5.6", now=self.at(9, 0, date(2026, 10, 7)))
            self.assertFalse(check_out(self.me, ip="103.4.5.6", now=self.at(23, 2, date(2026, 10, 7))).left_early)

            # The daily sheet works it out too, but a half day is never "early".
            save_attendance(day=date(2026, 10, 8), rows=[{"employee": self.me, "status": "present", "check_in": time(9, 0), "check_out": time(20, 0)}])
            self.assertTrue(Attendance.objects.get(employee=self.me, date=date(2026, 10, 8)).left_early)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("hr:attendance"), {"date": "2026-10-08"}), "Left early", count=2)  # the count and the row
        report = self.client.get(reverse("reports:report", args=["staff-attendance"]), {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertContains(report, "Left early")
        with tenant_context(self.tenant):
            save_attendance(day=date(2026, 10, 8), rows=[{"employee": self.me, "status": "half", "check_in": time(9, 0), "check_out": time(13, 0)}])
            self.assertFalse(Attendance.objects.get(employee=self.me, date=date(2026, 10, 8)).left_early)

    def test_pages(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("core:dashboard")), "Check in")
        self.assertEqual(self.client.get(reverse("hr:attendance_settings")).status_code, 403)
        self.client.force_login(self.owner)
        settings_url = reverse("hr:attendance_settings")
        self.assertContains(self.client.get(settings_url), "127.0.0.1")
        self.client.post(settings_url, {"action": "network", "net-address": "103.4.5.0/24", "net-label": "Main branch"})
        self.assertContains(self.client.post(settings_url, {"action": "network", "net-address": "not an ip"}), "isn")
        self.client.post(settings_url, {"action": "shift", "shift-name": "Evening", "shift-start": "15:00", "shift-end": "23:00",
                                        "shift-grace_minutes": "5"})
        with tenant_context(self.tenant):
            self.assertEqual(OfficeNetwork.objects.get().address, "103.4.5.0/24")
            evening = Shift.objects.get(name="Evening")
        self.client.post(settings_url, {"action": "assign", f"shifts-{self.me.pk}": [self.morning.pk, evening.pk]})
        with tenant_context(self.tenant):
            self.assertEqual(set(self.me.shifts.all()), {self.morning, evening})
        page = self.client.get(settings_url)
        self.assertContains(page, "09:00 AM – 11:00 PM")  # their whole day
        self.assertContains(page, f'id="sh-{self.me.pk}-{evening.pk}" value="{evening.pk}" checked')
        self.assertContains(self.client.get(reverse("hr:attendance")), "Morning + Evening")
        self.client.post(settings_url, {"action": "assign", f"shifts-{self.me.pk}": [evening.pk]})
        with tenant_context(self.tenant):
            self.assertEqual(list(self.me.shifts.all()), [evening])

        self.client.force_login(self.user)
        response = self.client.post(reverse("hr:check_in"), {"next": reverse("core:dashboard")}, REMOTE_ADDR="8.8.8.8", follow=True)
        self.assertContains(response, "Write why")
        with tenant_context(self.tenant):
            self.assertFalse(Attendance.objects.exists())
        self.client.post(reverse("hr:check_in"), {"reason": "Bank work first"}, REMOTE_ADDR="8.8.8.8")
        self.client.post(reverse("hr:check_out"), {}, REMOTE_ADDR="103.4.5.20")
        with tenant_context(self.tenant):
            record = Attendance.objects.get()
        self.assertEqual((record.in_reason, record.out_reason, record.out_ip), ("Bank work first", "", "103.4.5.20"))
        self.assertContains(self.client.get(reverse("hr:my_attendance")), "Bank work first")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("hr:attendance"), {"date": record.date.isoformat()}), "Bank work first")

    @override_settings(BEHIND_PROXY=True)
    def test_address_behind_a_proxy(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="103.4.5.6, 10.0.0.1")
        self.assertEqual(client_ip(request), "103.4.5.6")
