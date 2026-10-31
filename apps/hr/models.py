import ipaddress
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

MONEY = {"max_digits": 12, "decimal_places": 2, "default": Decimal("0")}
DAY = 1440  # minutes


def minutes(t):
    return t.hour * 60 + t.minute


class Shift(TenantOwnedModel):
    """Working hours. Checking in more than `grace_minutes` after the start counts as late."""

    name = models.CharField(_("name"), max_length=60)
    start = models.TimeField(_("starts at"))
    end = models.TimeField(_("ends at"))
    grace_minutes = models.PositiveSmallIntegerField(_("late after (minutes)"), default=10)

    class Meta:
        ordering = ["start", "name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_shift_name")]

    def __str__(self):
        return f"{self.name} ({self.start:%H:%M}–{self.end:%H:%M})"

    @property
    def length(self):
        """Minutes long, counted round the clock so a night shift (22:00–06:00) works too."""
        return (minutes(self.end) - minutes(self.start)) % DAY or DAY


class Schedule:
    """Someone's working day from their shift(s). On several it runs from the first one's start to the last one's end.
    The day begins after the longest break, so Night 22:00–06:00 + Morning 06:00–14:00 is one day from 22:00 to 14:00."""

    def __init__(self, shifts):
        shifts = sorted(shifts, key=lambda s: (s.start, s.name))

        def break_before(shift):
            others = [o for o in shifts if o is not shift]
            if any(0 < (minutes(shift.start) - minutes(o.start)) % DAY <= o.length for o in others):
                return 0  # carries straight on from another shift
            return min(((minutes(shift.start) - minutes(o.start) - o.length) % DAY for o in others), default=DAY - shift.length)

        first = max(shifts, key=break_before)  # ties: the earliest start

        def ends_after(shift):
            return (minutes(shift.start) - minutes(first.start)) % DAY + shift.length

        self.shifts = sorted(shifts, key=lambda s: (minutes(s.start) - minutes(first.start)) % DAY)
        self.first, self.last = first, max(self.shifts, key=ends_after)
        self.span = min(ends_after(self.last), DAY)

    @property
    def start(self):
        return self.first.start

    @property
    def end(self):
        return self.last.end

    @property
    def names(self):
        return " + ".join(s.name for s in self.shifts)

    def is_late(self, at):
        """Checking in more than the first shift's grace minutes after it starts. Arriving before the start is on time."""
        return self.first.grace_minutes < (minutes(at) - minutes(self.start)) % DAY < self.span

    def left_early(self, check_in, check_out):
        """Checked out before the last shift ends. Without a check-in time they're taken to have come at the start."""
        start = minutes(self.start)
        came = minutes(check_in) if check_in else start
        into = (came - start) % DAY  # how far into the day they arrived (a lot = they came before the start)
        to_end = self.span - into if into < self.span else self.span + DAY - into
        return (minutes(check_out) - came) % DAY < to_end


class OfficeNetwork(TenantOwnedModel):
    """An internet address (or range) of the shop. Checking in or out from anywhere else needs a reason."""

    label = models.CharField(_("name"), max_length=60, blank=True)
    address = models.CharField(_("IP address or range"), max_length=64)

    class Meta:
        ordering = ["label", "address"]
        constraints = [models.UniqueConstraint(fields=["tenant", "address"], name="uniq_office_network")]

    def __str__(self):
        return f"{self.label} ({self.address})" if self.label else self.address

    @property
    def network(self):
        return ipaddress.ip_network(self.address, strict=False)


class Attendance(TenantOwnedModel):
    """One person's day: present, late, absent… with optional in/out times."""

    class Status(models.TextChoices):
        PRESENT = "present", _("Present")
        LATE = "late", _("Late")
        HALF = "half", _("Half day")
        ABSENT = "absent", _("Absent")
        LEAVE = "leave", _("On leave")
        OFF = "off", _("Day off")

    employee = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField(_("date"))
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PRESENT)
    check_in = models.TimeField(_("in"), null=True, blank=True)
    check_out = models.TimeField(_("out"), null=True, blank=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Filled when the person checks in/out themselves: where from, and why if it wasn't the shop's network.
    self_marked = models.BooleanField(default=False)
    in_ip = models.GenericIPAddressField(null=True, blank=True)
    out_ip = models.GenericIPAddressField(null=True, blank=True)
    in_reason = models.CharField(_("reason for checking in elsewhere"), max_length=255, blank=True)
    out_reason = models.CharField(_("reason for checking out elsewhere"), max_length=255, blank=True)
    # Checked out before their last shift ended (only on a full day: present or late).
    left_early = models.BooleanField(_("left early"), default=False)

    class Meta:
        ordering = ["-date", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_per_day")]

    def __str__(self):
        return f"{self.employee} {self.date}: {self.status}"


class Advance(TenantOwnedModel):
    """Money given to staff ahead of their salary; payroll takes it back."""

    number = models.CharField(max_length=30)
    employee = models.ForeignKey("people.Employee", on_delete=models.PROTECT, related_name="advances", verbose_name=_("employee"))
    date = models.DateField(_("date"))
    amount = models.DecimalField(_("amount"), max_digits=12, decimal_places=2)
    payment_account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_advance_number")]

    def __str__(self):
        return self.number


class Payroll(TenantOwnedModel):
    """One month's pay for everyone. A draft can be changed; paying it posts the money and locks it."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PAID = "paid", _("Paid")

    number = models.CharField(max_length=30)
    month = models.DateField(_("month"))  # always the 1st
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    payment_account = models.ForeignKey("accounting.PaymentAccount", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    paid_on = models.DateField(null=True, blank=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "month"], name="one_payroll_per_month"),
            models.UniqueConstraint(fields=["tenant", "number"], name="uniq_payroll_number"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    def totals(self):
        lines = list(self.lines.all())
        return {
            "gross": sum((l.gross for l in lines), Decimal("0")), "advance": sum((l.advance_deduction for l in lines), Decimal("0")),
            "net": sum((l.net for l in lines), Decimal("0")), "people": len(lines),
        }


class PayrollLine(TenantOwnedModel):
    payroll = models.ForeignKey(Payroll, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("people.Employee", on_delete=models.PROTECT, related_name="payroll_lines")
    salary = models.DecimalField(**MONEY)
    allowance = models.DecimalField(**MONEY)
    sales = models.DecimalField(**MONEY)  # the net sales commission is worked out on
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    commission = models.DecimalField(**MONEY)
    absent_days = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("0"))  # a half day counts 0.5
    absence_deduction = models.DecimalField(**MONEY)
    bonus = models.DecimalField(**MONEY)
    other_deduction = models.DecimalField(**MONEY)
    advance_deduction = models.DecimalField(**MONEY)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee__name"]
        constraints = [models.UniqueConstraint(fields=["payroll", "employee"], name="one_line_per_employee")]

    @property
    def earnings(self):
        return self.salary + self.allowance + self.commission + self.bonus

    @property
    def gross(self):
        """What the month cost the shop: earnings less absence and other deductions."""
        return self.earnings - self.absence_deduction - self.other_deduction

    @property
    def net(self):
        """What is handed over: gross less the advance taken back."""
        return self.gross - self.advance_deduction

    @property
    def deductions(self):
        return self.absence_deduction + self.other_deduction + self.advance_deduction
