from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Outlet(TenantOwnedModel):
    """A branch of a pharmacy. Stock and sales are kept per outlet (screens come in Phase 2)."""

    name = models.CharField(_("name"), max_length=100)
    code = models.CharField(_("code"), max_length=20)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_outlet_code_per_tenant"),
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_outlet_name_per_tenant"),
        ]

    def __str__(self):
        return self.name
