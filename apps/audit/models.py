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
