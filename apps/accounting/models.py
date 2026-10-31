from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
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


MONEY = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0")}


class Account(TenantOwnedModel):
    """A line in the chart of accounts. Every journal line debits or credits one of these."""

    class Type(models.TextChoices):
        ASSET = "asset", _("Asset")
        LIABILITY = "liability", _("Liability")
        EQUITY = "equity", _("Equity")
        INCOME = "income", _("Income")
        EXPENSE = "expense", _("Expense")

    code = models.CharField(_("code"), max_length=10)
    name = models.CharField(_("name"), max_length=100)
    type = models.CharField(_("type"), max_length=10, choices=Type.choices)
    # Accounts the app posts to by itself ("sales", "inventory", …). Their name is shown translated and can't change.
    system_key = models.CharField(max_length=30, blank=True)
    payment_account = models.OneToOneField(PaymentAccount, on_delete=models.CASCADE, null=True, blank=True, related_name="ledger_account")
    description = models.CharField(_("description"), max_length=255, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_account_code_per_tenant",
                                    violation_error_message=_("Another account already has this code.")),
            models.UniqueConstraint(fields=["tenant", "system_key"], condition=~Q(system_key=""), name="uniq_system_account_per_tenant"),
        ]

    def __str__(self):
        return f"{self.code} · {self.label}"

    @property
    def label(self):
        from .chart import system_label

        if self.payment_account_id:
            return self.payment_account.name
        return system_label(self.system_key) or self.name

    @property
    def debit_normal(self):
        """Assets and expenses grow with debits; the rest with credits."""
        return self.type in (self.Type.ASSET, self.Type.EXPENSE)

    @property
    def is_locked(self):
        return bool(self.system_key or self.payment_account_id)


class JournalEntry(TenantOwnedModel):
    """One balanced double-entry transaction. Documents (sales, purchases, …) post their own; people can add manual ones."""

    number = models.CharField(max_length=30)
    date = models.DateField(_("date"))
    memo = models.CharField(_("description"), max_length=255, blank=True)
    source_type = models.CharField(max_length=30, blank=True)  # "sale", "purchase", … ; "" for manual entries
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_label = models.CharField(max_length=40, blank=True)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "number"], name="uniq_journal_number"),
            models.UniqueConstraint(fields=["tenant", "source_type", "source_id"], condition=Q(source_id__isnull=False),
                                    name="one_journal_per_document"),
        ]
        indexes = [models.Index(fields=["tenant", "date"], name="journal_date")]

    def __str__(self):
        return self.number

    @property
    def is_manual(self):
        return not self.source_type


class JournalLine(TenantOwnedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(**MONEY)
    credit = models.DecimalField(**MONEY)
    memo = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "account"], name="journal_line_account")]


class CashEntry(TenantOwnedModel):
    """Money in or out that isn't a sale or purchase: rent, salaries, electricity (expenses) or commission, scrap (income)."""

    class Kind(models.TextChoices):
        INCOME = "income", _("Income")
        EXPENSE = "expense", _("Expense")

    kind = models.CharField(max_length=10, choices=Kind.choices)
    number = models.CharField(max_length=30)
    date = models.DateField(_("date"))
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cash_entries", verbose_name=_("category"))
    payment_account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from / into"))
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    outlet = models.ForeignKey("outlets.Outlet", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("outlet"))
    register = models.ForeignKey("sales.CashRegister", on_delete=models.PROTECT, null=True, blank=True, related_name="cash_entries")
    reference = models.CharField(_("reference"), max_length=100, blank=True, help_text=_("Bill no., cheque no., transaction ID…"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_cash_entry_number")]

    def __str__(self):
        return self.number


class FundMovement(TenantOwnedModel):
    """Money moved between payment accounts, put in by the owner, or taken out by the owner."""

    class Kind(models.TextChoices):
        TRANSFER = "transfer", _("Move between accounts")
        DEPOSIT = "deposit", _("Owner puts money in")
        WITHDRAW = "withdraw", _("Owner takes money out")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices)
    number = models.CharField(max_length=30)
    date = models.DateField(_("date"))
    from_account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("from"))
    to_account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("to"))
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    register = models.ForeignKey("sales.CashRegister", on_delete=models.PROTECT, null=True, blank=True, related_name="fund_movements")
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "number"], name="uniq_fund_movement_number")]

    def __str__(self):
        return self.number
