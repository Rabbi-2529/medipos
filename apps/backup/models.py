from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class ExportLog(TenantOwnedModel):
    """Who downloaded the pharmacy's data, when, and what. Exports hold everything, so every one is written down."""

    class Kind(models.TextChoices):
        EXCEL = "excel", _("Excel workbook")
        BACKUP = "backup", _("Full backup")

    kind = models.CharField(max_length=10, choices=Kind.choices)
    datasets = models.JSONField(default=list, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    rows = models.PositiveIntegerField(default=0)
    size = models.PositiveBigIntegerField(default=0)  # bytes
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.created_at:%Y-%m-%d %H:%M}"
