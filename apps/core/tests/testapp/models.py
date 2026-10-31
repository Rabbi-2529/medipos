from django.db import models

from apps.core.models import TenantOwnedModel


class Widget(TenantOwnedModel):
    """Stand-in for real pharmacy data in tenancy tests."""

    name = models.CharField(max_length=50)

    class Meta:
        ordering = ["name"]
