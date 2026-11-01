from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class AuditEntry(TenantOwnedModel):
    """One thing someone did: added, changed or deleted a record, or signed in or out. Never edited afterwards."""

    class Action(models.TextChoices):
        CREATE = "create", _("Added")
        UPDATE = "update", _("Changed")
        DELETE = "delete", _("Deleted")
        LOGIN = "login", _("Signed in")
        LOGOUT = "logout", _("Signed out")
        LOGIN_FAILED = "login_failed", _("Wrong password")

    action = models.CharField(max_length=15, choices=Action.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    user_name = models.CharField(max_length=150, blank=True)  # kept if the login is removed later
    model = models.CharField(max_length=60, blank=True)  # "sales.sale"
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    # Changed: {field: [before, after]}. Added / deleted: {field: value} as it was.
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    path = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "created_at"]), models.Index(fields=["tenant", "model", "object_id"]),
                   models.Index(fields=["tenant", "user"])]

    def __str__(self):
        return f"{self.get_action_display()} {self.model} {self.object_repr}"


class BranchAudit(TenantOwnedModel):
    """Head office checking one branch over a period: its sales, cash, stock, money and books, then signing it off."""

    class Status(models.TextChoices):
        OPEN = "open", _("In progress")
        DONE = "done", _("Signed off")

    class Verdict(models.TextChoices):
        GOOD = "good", _("Satisfactory")
        WATCH = "watch", _("Needs improvement")
        BAD = "bad", _("Unsatisfactory")

    number = models.CharField(max_length=30)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, related_name="+", verbose_name=_("branch"))
    start = models.DateField(_("from"))
    end = models.DateField(_("to"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    figures = models.JSONField(default=dict, blank=True)  # the numbers as worked out; fixed once signed off
    figured_at = models.DateTimeField(null=True, blank=True)
    verdict = models.CharField(_("verdict"), max_length=10, choices=Verdict.choices, blank=True)
    summary = models.TextField(_("conclusion"), blank=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_branch_audit_number")]

    def __str__(self):
        return f"{self.number} · {self.outlet}"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN


class AuditCheck(TenantOwnedModel):
    """One line of the checklist: what the figures say, and the auditor's own verdict."""

    class Flag(models.TextChoices):
        OK = "ok", _("Looks right")
        WARN = "warn", _("Look closer")
        INFO = "info", _("For information")

    class Result(models.TextChoices):
        OK = "ok", _("OK")
        ISSUE = "issue", _("Issue")
        NA = "na", _("Not checked")

    audit = models.ForeignKey(BranchAudit, on_delete=models.CASCADE, related_name="checks")
    key = models.CharField(max_length=40)
    position = models.PositiveSmallIntegerField(default=0)
    flag = models.CharField(max_length=5, choices=Flag.choices, default=Flag.INFO)
    detail = models.CharField(max_length=300, blank=True)  # what the figures show, in words
    result = models.CharField(_("result"), max_length=6, choices=Result.choices, blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["audit", "key"], name="one_check_per_key")]


class AuditFinding(TenantOwnedModel):
    """Something the auditor found that needs doing."""

    class Severity(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")

    audit = models.ForeignKey(BranchAudit, on_delete=models.CASCADE, related_name="findings")
    title = models.CharField(_("finding"), max_length=150)
    severity = models.CharField(_("severity"), max_length=6, choices=Severity.choices, default=Severity.MEDIUM)
    detail = models.TextField(_("what was found"), blank=True)
    action = models.CharField(_("what to do"), max_length=255, blank=True)
    resolved = models.BooleanField(_("resolved"), default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["resolved", "id"]

    def __str__(self):
        return self.title
