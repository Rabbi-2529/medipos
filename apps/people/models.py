from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel
from apps.core.permissions import ALL_CODES


class Role(TenantOwnedModel):
    """A named set of permission codes ("outlet.create", …) given to employees."""

    name = models.CharField(_("name"), max_length=60)
    description = models.CharField(_("description"), max_length=255, blank=True)
    permissions = models.JSONField(default=list, blank=True)
    # Set on roles created from a template ("manager", "cashier") so later phases can grant them new modules.
    template_key = models.CharField(max_length=30, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_role_name_per_tenant",
                                               violation_error_message=_("A role with this name already exists."))]

    def __str__(self):
        return self.name

    @property
    def codes(self):
        """Granted codes for modules that exist (stored codes for future modules are kept but ignored)."""
        return frozenset(self.permissions) & ALL_CODES


class Employee(TenantOwnedModel):
    """Staff profile attached to a login. Every user of a pharmacy has one, the owner included."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee")
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="employees", verbose_name=_("role")
    )
    designation = models.CharField(_("designation"), max_length=60, blank=True, help_text=_("e.g. Pharmacist, Sales assistant"))
    all_outlets = models.BooleanField(_("all outlets"), default=False, help_text=_("Can work in every outlet, including new ones."))
    outlets = models.ManyToManyField("outlets.Outlet", blank=True, related_name="employees", verbose_name=_("outlets"))
    joined_on = models.DateField(_("joined on"), null=True, blank=True)
    nid = models.CharField(_("NID no."), max_length=30, blank=True)
    address = models.TextField(_("address"), blank=True)

    class Meta:
        ordering = ["user__first_name", "user__email"]

    def __str__(self):
        return str(self.user)
