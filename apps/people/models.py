from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.functional import cached_property
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
    """A member of staff. Most have a login; staff who never use the app (a delivery rider, a cleaner) don't."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="employee")
    # Kept in step with the login's name; for staff without a login it is their only name.
    name = models.CharField(_("name"), max_length=150, blank=True)
    phone = models.CharField(_("mobile"), max_length=20, blank=True)
    shifts = models.ManyToManyField("hr.Shift", blank=True, related_name="employees", verbose_name=_("shifts"))
    is_active = models.BooleanField(_("active"), default=True)
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="employees", verbose_name=_("role")
    )
    designation = models.CharField(_("designation"), max_length=60, blank=True, help_text=_("e.g. Pharmacist, Sales assistant"))
    all_outlets = models.BooleanField(_("all outlets"), default=False, help_text=_("Can work in every outlet, including new ones."))
    outlets = models.ManyToManyField("outlets.Outlet", blank=True, related_name="employees", verbose_name=_("outlets"))
    joined_on = models.DateField(_("joined on"), null=True, blank=True)
    nid = models.CharField(_("NID no."), max_length=30, blank=True)
    address = models.TextField(_("address"), blank=True)
    # Pay (used by payroll)
    salary = models.DecimalField(_("monthly salary"), max_digits=12, decimal_places=2, default=Decimal("0"))
    allowance = models.DecimalField(_("fixed allowances"), max_digits=12, decimal_places=2, default=Decimal("0"),
                                    help_text=_("Added to every month's pay, e.g. transport or mobile bill."))
    commission_percent = models.DecimalField(_("sales commission %"), max_digits=5, decimal_places=2, default=Decimal("0"),
                                             help_text=_("Of the net sales they make at the POS each month. 0 = none."))

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if self.user_id:
            self.name = self.user.get_full_name() or self.user.email
            self.phone = self.phone or self.user.phone or ""
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        if self.user_id:
            return self.user.get_full_name() or self.user.email
        return self.name

    @property
    def has_login(self):
        return self.user_id is not None

    @property
    def active(self):
        return self.user.is_active if self.user_id else self.is_active

    @cached_property
    def schedule(self):
        """Their working day from their shifts (hr.models.Schedule), or None when they have no shift."""
        from apps.hr.models import Schedule

        shifts = list(self.shifts.all())
        return Schedule(shifts) if shifts else None
