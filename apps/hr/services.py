"""Attendance, advances and payroll rules."""
import calendar
import ipaddress
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.formats import date_format, time_format
from django.utils.translation import gettext as _

from apps.accounting import posting
from apps.people.models import Employee
from apps.setup.services import next_code

from .models import Advance, Attendance, OfficeNetwork, Payroll, PayrollLine

ZERO = Decimal("0")
CENT = Decimal("0.01")
HALF = Decimal("0.5")


class HRError(Exception):
    """A user-facing reason an attendance, advance or payroll change was refused."""


def month_bounds(month):
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _paid(employee):
    return employee.salary > 0 or employee.allowance > 0 or employee.commission_percent > 0


def on_staff(employee):
    """Works here now: an active profile whose login (if any) is active too.
    The owner is left out unless they pay themselves a salary."""
    return employee.active and not (employee.user_id and employee.user.is_owner and not _paid(employee))


def active_staff():
    return [e for e in Employee.objects.select_related("user").prefetch_related("shifts").order_by("name") if on_staff(e)]


# ---- Attendance -----------------------------------------------------------------------

FULL_DAY = (Attendance.Status.PRESENT, Attendance.Status.LATE)  # the days leaving before the last shift ends counts on


def leaves_early(employee, status, check_in, check_out):
    schedule = employee.schedule
    return bool(check_out and status in FULL_DAY and schedule and schedule.left_early(check_in, check_out))


@transaction.atomic
def save_attendance(*, day, rows, user=None):
    """rows: [{employee, status, check_in, check_out, note}]. An empty status clears that person's day."""
    saved = 0
    for row in rows:
        employee, status = row["employee"], row.get("status") or ""
        if not status:
            Attendance.objects.filter(employee=employee, date=day).delete()
            continue
        if status not in Attendance.Status.values:
            raise HRError(_("Unknown attendance status."))
        check_in, check_out = row.get("check_in"), row.get("check_out")
        Attendance.objects.update_or_create(employee=employee, date=day, defaults={
            "status": status, "check_in": check_in, "check_out": check_out, "note": (row.get("note") or "")[:255], "marked_by": user,
            "left_early": leaves_early(employee, status, check_in, check_out)})
        saved += 1
    return saved


def attendance_counts(employee_ids, start, end):
    """{employee_id: Counter(status -> days)} between two dates."""
    counts = defaultdict(Counter)
    rows = Attendance.objects.filter(employee_id__in=employee_ids, date__gte=start, date__lte=end).values("employee", "status")
    for row in rows:
        counts[row["employee"]][row["status"]] += 1
    return counts


def absent_days(counts):
    return Decimal(counts.get(Attendance.Status.ABSENT, 0)) + HALF * counts.get(Attendance.Status.HALF, 0)


# ---- Advances -------------------------------------------------------------------------

def outstanding_advance(employee, exclude=None):
    """Advances given minus what paid payrolls (and `exclude`'s draft is not one of them) took back."""
    given = Advance.objects.filter(employee=employee).aggregate(t=Sum("amount"))["t"] or ZERO
    taken = (PayrollLine.objects.filter(employee=employee, payroll__status=Payroll.Status.PAID)
             .exclude(payroll=exclude).aggregate(t=Sum("advance_deduction"))["t"] or ZERO)
    return given - taken


def outstanding_by_employee():
    """{employee_id: still owed} for everyone who has ever had an advance."""
    owed = defaultdict(lambda: ZERO)
    for row in Advance.objects.values("employee").annotate(t=Sum("amount")):
        owed[row["employee"]] += row["t"]
    paid = PayrollLine.objects.filter(payroll__status=Payroll.Status.PAID, advance_deduction__gt=0)
    for row in paid.values("employee").annotate(t=Sum("advance_deduction")):
        owed[row["employee"]] -= row["t"]
    return dict(owed)


@transaction.atomic
def give_advance(*, tenant, employee, date, amount, payment_account, note="", user=None):
    if amount is None or amount <= 0:
        raise HRError(_("Amount must be more than zero."))
    advance = Advance.objects.create(number=next_code(tenant, "advance", prefix="ADV-", padding=5), employee=employee, date=date,
                                     amount=amount, payment_account=payment_account, note=note, created_by=user)
    posting.post_advance(advance)
    return advance


@transaction.atomic
def delete_advance(advance):
    """Only while no paid payroll has taken it back (what is still owed covers the whole amount)."""
    if outstanding_advance(advance.employee) < advance.amount:
        raise HRError(_("%(number)s was already taken back in a payroll, so it can't be deleted.") % {"number": advance.number})
    posting.unpost("advance", advance.pk)
    advance.delete()


# ---- Payroll --------------------------------------------------------------------------

def net_sales_by(user, start, end):
    """What someone sold at the POS in the period, less returns of those sales."""
    from apps.sales.models import Sale, SaleReturn

    if user is None:
        return ZERO
    sold = Sale.objects.filter(created_by=user, sold_at__date__gte=start, sold_at__date__lte=end).aggregate(t=Sum("total"))["t"] or ZERO
    returned = (SaleReturn.objects.filter(sale__created_by=user, date__gte=start, date__lte=end).aggregate(t=Sum("total"))["t"] or ZERO)
    return sold - returned


def _fill(line, first, last, counts, payroll=None):
    """Work out the automatic parts of a line: pay, commission, absences, and how much advance to take back."""
    employee = line.employee
    line.salary, line.allowance, line.commission_percent = employee.salary, employee.allowance, employee.commission_percent
    line.sales = net_sales_by(employee.user, first, last) if employee.commission_percent else ZERO
    line.commission = (line.sales * employee.commission_percent / 100).quantize(CENT, ROUND_HALF_UP)
    line.absent_days = absent_days(counts.get(employee.pk, {}))
    days = (last - first).days + 1
    line.absence_deduction = min((employee.salary / days * line.absent_days).quantize(CENT, ROUND_HALF_UP), employee.salary)
    owed = outstanding_advance(employee, exclude=payroll)
    line.advance_deduction = max(min(owed, line.gross), ZERO)


@transaction.atomic
def prepare_payroll(*, tenant, month, user=None):
    """A draft for the month with a line for everyone on pay. One payroll per month."""
    first, last = month_bounds(month)
    if Payroll.objects.filter(month=first).exists():
        raise HRError(_("There is already a payroll for %(month)s.") % {"month": date_format(first, "F Y")})
    staff = [e for e in active_staff() if _paid(e)]
    if not staff:
        raise HRError(_("Nobody has a salary yet. Set salaries on the employees first."))
    payroll = Payroll.objects.create(number=next_code(tenant, "payroll", prefix="PAY-", padding=4), month=first, created_by=user)
    counts = attendance_counts([e.pk for e in staff], first, last)
    for employee in staff:
        line = PayrollLine(payroll=payroll, employee=employee)
        _fill(line, first, last, counts, payroll)
        line.save()
    return payroll


def _draft(payroll):
    if not payroll.is_draft:
        raise HRError(_("%(number)s is already paid and can't change.") % {"number": payroll.number})


@transaction.atomic
def recalculate(payroll):
    """Refresh salaries, commission, absences and advances from today's data; bonuses, other deductions and notes stay."""
    _draft(payroll)
    first, last = month_bounds(payroll.month)
    lines = {l.employee_id: l for l in payroll.lines.select_related("employee", "employee__user")}
    for employee in active_staff():
        if _paid(employee) and employee.pk not in lines:
            lines[employee.pk] = PayrollLine(payroll=payroll, employee=employee)
    counts = attendance_counts(list(lines), first, last)
    for line in lines.values():
        _fill(line, first, last, counts, payroll)
        line.save()
    return payroll


def _money(value, label):
    try:
        amount = Decimal(str(value or 0)).quantize(CENT)
    except (InvalidOperation, ValueError):
        raise HRError(_("%(field)s: enter a number.") % {"field": label})
    if amount < 0:
        raise HRError(_("%(field)s can't be negative.") % {"field": label})
    return amount


@transaction.atomic
def update_lines(payroll, changes):
    """changes: {line_id: {bonus, other_deduction, absence_deduction, advance_deduction, note}} typed on the draft."""
    _draft(payroll)
    for line in payroll.lines.select_related("employee"):
        values = changes.get(line.pk)
        if not values:
            continue
        name = line.employee.display_name
        line.bonus = _money(values.get("bonus"), _("Bonus"))
        line.other_deduction = _money(values.get("other_deduction"), _("Other deduction"))
        line.absence_deduction = _money(values.get("absence_deduction"), _("Absence deduction"))
        line.advance_deduction = _money(values.get("advance_deduction"), _("Advance"))
        line.note = str(values.get("note") or "")[:255]
        if line.gross < 0:
            raise HRError(_("%(name)s: deductions are more than the pay.") % {"name": name})
        owed = outstanding_advance(line.employee, exclude=payroll)
        if line.advance_deduction > owed:
            raise HRError(_("%(name)s only owes %(owed)s in advances.") % {"name": name, "owed": owed})
        if line.net < 0:
            raise HRError(_("%(name)s: the advance taken back is more than the pay.") % {"name": name})
        line.save()
    return payroll


@transaction.atomic
def pay_payroll(payroll, *, account, day, user=None):
    """Hand out the month's pay from one account. The books get the salary expense; the payroll locks."""
    payroll = Payroll.objects.select_for_update().get(pk=payroll.pk)
    _draft(payroll)
    if not payroll.lines.exists():
        raise HRError(_("This payroll has nobody on it."))
    payroll.status, payroll.payment_account, payroll.paid_on, payroll.paid_by = Payroll.Status.PAID, account, day, user
    payroll.save(update_fields=["status", "payment_account", "paid_on", "paid_by", "updated_at"])
    posting.post_payroll(payroll)
    return payroll


def delete_draft(payroll):
    _draft(payroll)
    payroll.delete()


def first_of(value):
    """'2026-10' or a date -> the 1st of that month."""
    if isinstance(value, date):
        return value.replace(day=1)
    year, month = str(value).split("-")[:2]
    return date(int(year), int(month), 1)


# ---- Shifts, the shop's networks and checking in yourself ----------------------------------------

def client_ip(request):
    """The address the request came from. Behind a reverse proxy (BEHIND_PROXY=True) it is the first X-Forwarded-For hop."""
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR", "")


def clean_network(value):
    """'103.4.5.6' or '103.4.5.0/24' -> the tidy form, or HRError."""
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value)) if "/" not in value else str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        raise HRError(_("“%(value)s” isn't an IP address or range (e.g. 103.4.5.6 or 103.4.5.0/24).") % {"value": value})


def on_office_network(ip, networks=None):
    """True when no networks are set (no check), or `ip` is inside one of them."""
    networks = list(OfficeNetwork.objects.all()) if networks is None else networks
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in n.network for n in networks)


def open_record(employee, now=None):
    """Today's (or a night shift's yesterday's) attendance that has a check-in but no check-out yet."""
    today = timezone.localtime(now or timezone.now()).date()
    return (Attendance.objects.filter(employee=employee, date__in=[today, today - timedelta(days=1)], check_in__isnull=False,
                                      check_out__isnull=True).order_by("-date").first())


def _reason(ip, reason, word):
    reason = (reason or "").strip()[:255]
    if on_office_network(ip):
        return ""
    if not reason:
        raise HRError(word)
    return reason


@transaction.atomic
def check_in(employee, *, ip, reason="", now=None):
    now = timezone.localtime(now or timezone.now())
    day, at = now.date(), now.time().replace(second=0, microsecond=0)
    reason = _reason(ip, reason, _("You aren't on the shop's network. Write why you're checking in from somewhere else."))
    record = Attendance.objects.select_for_update().filter(employee=employee, date=day).first()
    if record and record.check_in:
        raise HRError(_("You already checked in today at %(time)s.") % {"time": time_format(record.check_in, "h:i A")})
    schedule = employee.schedule
    status = Attendance.Status.LATE if schedule and schedule.is_late(at) else Attendance.Status.PRESENT
    if record is None:
        record = Attendance(employee=employee, date=day, status=status)
    elif record.status in (Attendance.Status.ABSENT, Attendance.Status.LEAVE, Attendance.Status.OFF):
        record.status = status  # they came in after all
    record.check_in, record.in_ip, record.in_reason = at, ip or None, reason
    record.self_marked, record.marked_by = True, employee.user
    record.save()
    return record


@transaction.atomic
def check_out(employee, *, ip, reason="", now=None):
    now = timezone.localtime(now or timezone.now())
    record = open_record(employee, now)
    if record is None:
        raise HRError(_("You haven't checked in yet."))
    reason = _reason(ip, reason, _("You aren't on the shop's network. Write why you're checking out from somewhere else."))
    record.check_out, record.out_ip, record.out_reason = now.time().replace(second=0, microsecond=0), ip or None, reason
    record.left_early = leaves_early(employee, record.status, record.check_in, record.check_out)
    record.self_marked = True
    record.save(update_fields=["check_out", "out_ip", "out_reason", "left_early", "self_marked", "updated_at"])
    return record
