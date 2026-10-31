from django.db import models

from .tenancy import TenantMissingError, get_current_tenant, is_tenant_bypassed


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantManager(models.Manager):
    """Scopes every query to the current tenant. Fails closed: no tenant -> no rows."""

    def get_queryset(self):
        qs = super().get_queryset()
        if is_tenant_bypassed():
            return qs
        tenant = get_current_tenant()
        if tenant is None:
            return qs.none()
        return qs.filter(tenant=tenant)


class TenantOwnedModel(TimeStampedModel):
    """Base for all pharmacy data. `objects` is tenant-scoped; `all_objects` is not."""

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="+", editable=False
    )

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            tenant = get_current_tenant()
            if tenant is None:
                raise TenantMissingError(
                    f"Cannot save {type(self).__name__} without an active tenant."
                )
            self.tenant = tenant
        super().save(*args, **kwargs)
