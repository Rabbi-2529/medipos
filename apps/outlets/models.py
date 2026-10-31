from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Outlet(TenantOwnedModel):
    """A branch of a pharmacy. Stock and sales are kept per outlet."""

    name = models.CharField(_("name"), max_length=100)
    code = models.CharField(_("code"), max_length=20)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    email = models.EmailField(_("email"), blank=True)
    address = models.TextField(_("address"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_outlet_code_per_tenant",
                                    violation_error_message=_("An outlet with this code already exists.")),
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_outlet_name_per_tenant",
                                    violation_error_message=_("An outlet with this name already exists.")),
        ]

    def __str__(self):
        return self.name


class Counter(TenantOwnedModel):
    """A till inside an outlet. Cash registers are opened per counter (Phase 5)."""

    outlet = models.ForeignKey(Outlet, on_delete=models.CASCADE, related_name="counters", verbose_name=_("outlet"))
    name = models.CharField(_("name"), max_length=60)
    description = models.CharField(_("description"), max_length=255, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["outlet__code", "name"]
        constraints = [models.UniqueConstraint(fields=["outlet", "name"], name="uniq_counter_name_per_outlet",
                                               violation_error_message=_("This outlet already has a counter with that name."))]

    def __str__(self):
        return f"{self.outlet} · {self.name}"
