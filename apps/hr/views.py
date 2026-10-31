from collections import Counter
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.formats import date_format, time_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounting.forms import DateRangeForm
from apps.accounting.models import JournalEntry
from apps.people.access import has_perm, shop_permission_required
from apps.people.models import Employee
from apps.setup.services import format_money, get_business_settings

from .forms import AdvanceForm, NetworkForm, PayForm, PrepareForm, ShiftForm
from .models import Advance, Attendance, OfficeNetwork, Payroll, Shift
from .services import (
    FULL_DAY, HRError, active_staff, attendance_counts, check_in, check_out, client_ip, delete_advance, delete_draft, give_advance,
    month_bounds, on_office_network, on_staff, open_record, outstanding_advance, outstanding_by_employee, pay_payroll, prepare_payroll,
    recalculate, save_attendance, update_lines,
)

S = Attendance.Status
# Status -> (colour family, icon). Colour always comes with the label, never alone.
STATUS_LOOK = {
    S.PRESENT: ("teal", "bi-check-circle"), S.LATE: ("amber", "bi-alarm"), S.HALF: ("orange", "bi-circle-half"),
    S.ABSENT: ("red", "bi-x-circle"), S.LEAVE: ("sky", "bi-door-open"), S.OFF: ("slate", "bi-moon"),
}
SHORT = {S.PRESENT: gettext_lazy("P"), S.LATE: gettext_lazy("L"), S.HALF: gettext_lazy("H"), S.ABSENT: gettext_lazy("A"),
         S.LEAVE: gettext_lazy("LV"), S.OFF: gettext_lazy("O")}


def _statuses():
    return [{"value": value, "label": label, "tone": STATUS_LOOK[value][0], "icon": STATUS_LOOK[value][1], "short": SHORT[value]}
            for value, label in S.choices]


# ---- Attendance -----------------------------------------------------------------------

def _day(value):
    return parse_date(value or "") or timezone.localdate()


def _time(value):
    try:
        return datetime.strptime(value or "", "%H:%M").time()
    except ValueError:
        return None


@shop_permission_required("attendance.list")
def attendance(request):
    """The day's sheet: everyone on staff with a status, in/out time and a note."""
    day = _day(request.POST.get("date") if request.method == "POST" else request.GET.get("date"))
    can_mark = has_perm(request.user, "attendance.create")
    staff = active_staff()
    records = {a.employee_id: a for a in Attendance.objects.filter(date=day).select_related("employee", "marked_by")}
    on_staff = {e.pk for e in staff}
    # People who have left still show on days they were marked.
    people = staff + sorted((r.employee for r in records.values() if r.employee_id not in on_staff), key=lambda e: e.name)
    if request.method == "POST":
        if not can_mark:
            raise Http404
        rows = [{
            "employee": e, "status": request.POST.get(f"status-{e.pk}", ""), "check_in": _time(request.POST.get(f"in-{e.pk}")),
            "check_out": _time(request.POST.get(f"out-{e.pk}")), "note": request.POST.get(f"note-{e.pk}", "").strip(),
        } for e in people]
        try:
            saved = save_attendance(day=day, rows=rows, user=request.user)
        except HRError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Attendance for %(date)s saved: %(count)s marked.") % {
                "date": date_format(day, "j F Y"), "count": saved})
        return redirect(f"{reverse('hr:attendance')}?date={day:%Y-%m-%d}")
    counts = Counter(r.status for r in records.values())
    statuses = _statuses()
    for status in statuses:
        status["count"] = counts.get(status["value"], 0)
    return render(request, "hr/attendance.html", {
        "day": day, "prev": day - timedelta(days=1), "next": day + timedelta(days=1), "today": timezone.localdate(),
        "rows": [{"employee": e, "record": records.get(e.pk)} for e in people], "statuses": statuses,
        "unmarked": len(people) - len(records), "can_mark": can_mark, "left_early": sum(r.left_early for r in records.values()),
    })


@shop_permission_required("attendance.list")
def attendance_calendar(request):
    employee = request.GET.get("employee", "")
    return render(request, "hr/attendance_calendar.html", {
        "people": Employee.objects.filter(Q(pk__in=[e.pk for e in active_staff()]) | Q(attendance__isnull=False)).distinct().order_by("name"),
        "employee": employee, "statuses": _statuses(), "can_mark": has_perm(request.user, "attendance.create"),
    })


@shop_permission_required("attendance.list")
def attendance_events(request):
    """FullCalendar's feed. One person: their day with times. Everyone: how many had each status that day."""
    start, end = parse_date((request.GET.get("start") or "")[:10]), parse_date((request.GET.get("end") or "")[:10])
    if not start or not end or (end - start).days > 62:
        return JsonResponse([], safe=False)
    order = {value: i for i, value in enumerate(S.values)}
    labels = dict(S.choices)
    records = Attendance.objects.filter(date__gte=start, date__lt=end)
    events = []
    if request.GET.get("employee", "").isdigit():
        for a in records.filter(employee_id=request.GET["employee"]):
            times = "–".join(t.strftime("%H:%M") for t in (a.check_in, a.check_out) if t)
            away = [text for text in (a.in_reason and f"{_('In')}: {a.in_reason}", a.out_reason and f"{_('Out')}: {a.out_reason}") if text]
            early = _("Left early") if a.left_early else ""
            events.append({"title": str(labels[a.status]), "start": a.date.isoformat(), "allDay": True,
                           "classNames": ["att-event", f"att-{a.status}"],
                           "extendedProps": {"status": a.status, "count": 1, "order": order[a.status], "times": times,
                                             "note": "\n".join(filter(None, [early, a.note, *away])), "away": bool(away),
                                             "early": a.left_early}})
    else:
        for row in records.values("date", "status").annotate(n=Count("id")).order_by():
            events.append({"title": f"{labels[row['status']]} · {row['n']}", "start": row["date"].isoformat(), "allDay": True,
                           "classNames": ["att-event", f"att-{row['status']}"],
                           "extendedProps": {"status": row["status"], "count": row["n"], "order": order[row["status"]]}})
    return JsonResponse(events, safe=False)


# ---- Advances -------------------------------------------------------------------------

@shop_permission_required("salary.list")
def advances(request):
    settings = get_business_settings(request.tenant)
    can_give = has_perm(request.user, "salary.create")
    form = AdvanceForm(request.POST or None)
    if request.method == "POST":
        if not can_give:
            raise Http404
        if form.is_valid():
            data = form.cleaned_data
            try:
                advance = give_advance(tenant=request.tenant, employee=data["employee"], date=data["date"], amount=data["amount"],
                                       payment_account=data["payment_account"], note=data["note"], user=request.user)
            except HRError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("%(number)s saved: %(amount)s to %(name)s. The next payroll takes it back.") % {
                    "number": advance.number, "amount": format_money(advance.amount, settings), "name": advance.employee.display_name})
                return redirect("hr:advances")
    start, end = DateRangeForm(request.GET).range()
    owed = {pk: amount for pk, amount in outstanding_by_employee().items() if amount > 0}
    owing = sorted(Employee.objects.filter(pk__in=owed), key=lambda e: -owed[e.pk])
    return render(request, "hr/advances.html", {
        "form": form, "start": start, "end": end, "can_give": can_give,
        "advances": Advance.objects.filter(date__gte=start, date__lte=end).select_related("employee", "payment_account", "created_by"),
        "owing": [(e, owed[e.pk]) for e in owing], "owed_total": sum(owed.values()),
    })


@shop_permission_required("salary.create")
@require_POST
def advance_delete(request, pk):
    advance = get_object_or_404(Advance.objects.select_related("employee"), pk=pk)
    try:
        delete_advance(advance)
    except HRError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("%(number)s deleted.") % {"number": advance.number})
    return redirect("hr:advances")


# ---- Payroll --------------------------------------------------------------------------

def _suggested_month():
    """The first of last month, this month and next month that has no payroll yet."""
    this = timezone.localdate().replace(day=1)
    months = [(this - timedelta(days=1)).replace(day=1), this, (this + timedelta(days=32)).replace(day=1)]
    done = set(Payroll.objects.filter(month__in=months).values_list("month", flat=True))
    return next((m for m in months if m not in done), this)


@shop_permission_required("salary.list")
def payroll_list(request):
    can_manage = has_perm(request.user, "salary.create")
    form = PrepareForm(request.POST or None, initial={"month": _suggested_month()})
    if request.method == "POST":
        if not can_manage:
            raise Http404
        if form.is_valid():
            try:
                payroll = prepare_payroll(tenant=request.tenant, month=form.cleaned_data["month"], user=request.user)
            except HRError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("%(number)s is ready as a draft. Check it, then pay.") % {"number": payroll.number})
                return redirect("hr:payroll_detail", payroll.pk)
    payrolls = list(Payroll.objects.select_related("payment_account").prefetch_related("lines"))
    for payroll in payrolls:
        payroll.sums = payroll.totals()
    return render(request, "hr/payroll_list.html", {
        "form": form, "payrolls": payrolls, "can_manage": can_manage,
        "staff_count": len(active_staff()), "unpaid_staff": [e for e in active_staff() if not (e.salary or e.allowance or e.commission_percent)],
    })


def _payroll(pk):
    return get_object_or_404(Payroll.objects.select_related("payment_account", "created_by", "paid_by"), pk=pk)


EDITABLE = ("absence_deduction", "bonus", "other_deduction", "advance_deduction", "note")
PAY_MARKS = (S.HALF, S.ABSENT, S.LEAVE)  # the days worth a second look before paying


@shop_permission_required("salary.list")
def payroll_detail(request, pk):
    payroll = _payroll(pk)
    can_manage = has_perm(request.user, "salary.create") and payroll.is_draft
    lines = list(payroll.lines.select_related("employee", "employee__user"))
    if request.method == "POST":
        if not can_manage:
            raise Http404
        try:
            if request.POST.get("action") == "recalculate":
                recalculate(payroll)
                messages.success(request, _("Salaries, commission, absences and advances were worked out again."))
            else:
                update_lines(payroll, {line.pk: {f: request.POST.get(f"{f}-{line.pk}") for f in EDITABLE} for line in lines})
                messages.success(request, _("Saved."))
        except HRError as error:
            messages.error(request, str(error))
        return redirect("hr:payroll_detail", payroll.pk)
    first, last = month_bounds(payroll.month)
    counts = attendance_counts([line.employee_id for line in lines], first, last)
    statuses = _statuses()
    for line in lines:
        days = counts.get(line.employee_id, {})
        line.marks = [{**s, "count": days[s["value"]]} for s in statuses if s["value"] in PAY_MARKS and days.get(s["value"])]
        if can_manage:
            line.owed = outstanding_advance(line.employee, exclude=payroll)
    entry = None if payroll.is_draft else JournalEntry.objects.filter(source_type="payroll", source_id=payroll.pk).first()
    return render(request, "hr/payroll_detail.html", {
        "payroll": payroll, "lines": lines, "totals": payroll.totals(), "can_manage": can_manage, "entry": entry,
        "pay_form": PayForm() if can_manage else None,
        "shop": get_business_settings(request.tenant),
    })


@shop_permission_required("salary.create")
@require_POST
def payroll_pay(request, pk):
    payroll = _payroll(pk)
    form = PayForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Pick the account and the date the salaries were paid."))
        return redirect("hr:payroll_detail", payroll.pk)
    try:
        pay_payroll(payroll, account=form.cleaned_data["payment_account"], day=form.cleaned_data["date"], user=request.user)
    except HRError as error:
        messages.error(request, str(error))
    else:
        totals = payroll.totals()
        messages.success(request, _("%(number)s paid: %(amount)s from %(account)s.") % {
            "number": payroll.number, "amount": format_money(totals["net"], get_business_settings(request.tenant)),
            "account": form.cleaned_data["payment_account"].name})
    return redirect("hr:payroll_detail", payroll.pk)


@shop_permission_required("salary.create")
@require_POST
def payroll_delete(request, pk):
    payroll = _payroll(pk)
    try:
        delete_draft(payroll)
    except HRError as error:
        messages.error(request, str(error))
        return redirect("hr:payroll_detail", payroll.pk)
    messages.success(request, _("%(number)s deleted.") % {"number": payroll.number})
    return redirect("hr:payroll")


@shop_permission_required("salary.list")
def payslip(request, pk, line_pk):
    payroll = _payroll(pk)
    line = get_object_or_404(payroll.lines.select_related("employee", "employee__user"), pk=line_pk)
    first, last = month_bounds(payroll.month)
    days = attendance_counts([line.employee_id], first, last).get(line.employee_id, {})
    statuses = _statuses()
    for status in statuses:
        status["count"] = days.get(status["value"], 0)
    return render(request, "hr/payslip.html", {
        "payroll": payroll, "line": line, "statuses": statuses, "first": first, "last": last,
        "shop": get_business_settings(request.tenant), "tenant": request.tenant, "auto_print": request.GET.get("print") == "1",
    })


# ---- Shifts and the shop's networks ---------------------------------------------------------

@shop_permission_required("attendance.edit")
def attendance_settings(request):
    """Shifts, who works which, and the internet addresses staff check in from without giving a reason."""
    editing = Shift.objects.filter(pk=request.GET["shift"]).first() if request.GET.get("shift", "").isdigit() else None
    shift_form = ShiftForm(prefix="shift", instance=editing)
    ip, networks = client_ip(request), list(OfficeNetwork.objects.all())
    ip_listed = bool(networks) and on_office_network(ip, networks)
    network_form = NetworkForm(prefix="net", initial={"address": "" if ip_listed else ip})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "shift":
            instance = get_object_or_404(Shift, pk=request.POST["pk"]) if request.POST.get("pk") else None
            shift_form = ShiftForm(request.POST, prefix="shift", instance=instance)
            if shift_form.is_valid():
                shift = shift_form.save()
                messages.success(request, _("Shift “%(name)s” saved.") % {"name": shift.name})
                return redirect("hr:attendance_settings")
            editing = instance
        elif action == "shift_delete":
            shift = get_object_or_404(Shift, pk=request.POST.get("pk"))
            shift.delete()
            messages.success(request, _("Shift “%(name)s” deleted and taken off everyone who worked it.") % {"name": shift.name})
            return redirect("hr:attendance_settings")
        elif action == "network":
            network_form = NetworkForm(request.POST, prefix="net")
            if network_form.is_valid():
                network = network_form.save()
                messages.success(request, _("%(address)s added.") % {"address": network.address})
                return redirect("hr:attendance_settings")
        elif action == "network_delete":
            network = get_object_or_404(OfficeNetwork, pk=request.POST.get("pk"))
            network.delete()
            messages.success(request, _("%(address)s removed.") % {"address": network.address})
            return redirect("hr:attendance_settings")
        elif action == "assign":
            shifts = {str(s.pk): s for s in Shift.objects.all()}
            for employee in active_staff():
                picked = {shifts[pk] for pk in request.POST.getlist(f"shifts-{employee.pk}") if pk in shifts}
                if picked != set(employee.shifts.all()):
                    employee.shifts.set(picked)
            messages.success(request, _("Shifts saved."))
            return redirect("hr:attendance_settings")
    staff = active_staff()
    shifts = list(Shift.objects.all())
    for shift in shifts:
        shift.people = [e for e in staff if shift in e.shifts.all()]
    for employee in staff:
        employee.shift_ids = {s.pk for s in employee.shifts.all()}
    return render(request, "hr/attendance_settings.html", {
        "shifts": shifts, "shift_form": shift_form, "editing": editing,
        "networks": networks, "network_form": network_form, "staff": staff,
        "ip": ip, "ip_listed": ip_listed,
    })


# ---- Checking in and out yourself ---------------------------------------------------------

def _me(request):
    """The logged-in person's own staff profile, if they are on staff."""
    if getattr(request, "tenant", None) is None:
        return None
    employee = Employee.objects.select_related("user").prefetch_related("shifts").filter(user=request.user).first()
    return employee if employee and on_staff(employee) else None


def check_panel(request):
    """What the check-in card on the dashboard and "My attendance" need; None for people who aren't on staff."""
    employee = _me(request)
    if employee is None:
        return None
    today = timezone.localdate()
    ip, networks = client_ip(request), list(OfficeNetwork.objects.all())
    record = Attendance.objects.filter(employee=employee, date=today).first()
    open_ = open_record(employee)
    look = {s["value"]: s for s in _statuses()}
    schedule, now = employee.schedule, timezone.localtime().time()
    return {
        "employee": employee, "schedule": schedule, "today": today, "record": record, "open": open_,
        # Checking out now would be before the last shift ends.
        "leaving_early": bool(open_ and schedule and open_.status in FULL_DAY and schedule.left_early(open_.check_in, now)),
        "status": look.get(record.status) if record else None,
        "can_check_in": not (record and record.check_in) and open_ is None, "can_check_out": open_ is not None,
        "ip": ip, "networks_set": bool(networks), "on_network": on_office_network(ip, networks),
    }


@login_required
@require_POST
def self_check(request, action):
    employee = _me(request)
    if employee is None:
        raise Http404
    ip, reason = client_ip(request), request.POST.get("reason", "")
    try:
        if action == "in":
            record = check_in(employee, ip=ip, reason=reason)
            late = record.status == S.LATE
            messages.success(request, (_("Checked in at %(time)s. You're late today.") if late else _("Checked in at %(time)s.")) % {
                "time": time_format(record.check_in, "h:i A")})
        else:
            record = check_out(employee, ip=ip, reason=reason)
            if record.left_early:
                messages.warning(request, _("Checked out at %(time)s, before your shift ends at %(end)s. It's marked as leaving early.") % {
                    "time": time_format(record.check_out, "h:i A"), "end": time_format(employee.schedule.end, "h:i A")})
            else:
                messages.success(request, _("Checked out at %(time)s.") % {"time": time_format(record.check_out, "h:i A")})
    except HRError as error:
        messages.error(request, str(error))
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse("hr:my_attendance")
    return redirect(target)


@login_required
def my_attendance(request):
    panel = check_panel(request)
    if panel is None:
        raise Http404
    first, last = month_bounds(timezone.localdate())
    records = list(Attendance.objects.filter(employee=panel["employee"], date__gte=first, date__lte=last).order_by("-date"))
    statuses = _statuses()
    look = {s["value"]: s for s in statuses}
    counts = Counter(r.status for r in records)
    for record in records:
        record.look = look[record.status]
    for status in statuses:
        status["count"] = counts.get(status["value"], 0)
    return render(request, "hr/my_attendance.html", {"panel": panel, "records": records, "statuses": statuses, "month": first,
                                                     "left_early": sum(r.left_early for r in records)})
