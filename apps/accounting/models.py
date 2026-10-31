from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class PaymentAccount(TenantOwnedModel):
    """Where money is received or paid from: cash drawer, bank account, bKash/Nagad wallet, card machine."""

    class Kind(models.TextChoices):
        CASH = "cash", _("Cash")
        BANK = "bank", _("Bank account")
        MOBILE = "mobile", _("Mobile banking")
        CARD = "card", _("Card")
        OTHER = "other", _("Other")

    name = models.CharField(_("name"), max_length=60)
    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.CASH)
    provider = models.CharField(_("bank / provider"), max_length=100, blank=True, help_text=_("e.g. Dutch-Bangla Bank, bKash"))
    account_number = models.CharField(_("account / wallet no."), max_length=50, blank=True)
    opening_balance = models.DecimalField(_("opening balance"), max_digits=14, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(_("active"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_payment_account_name_per_tenant",
                                               violation_error_message=_("A payment account with this name already exists."))]

    def __str__(self):
        return self.name
