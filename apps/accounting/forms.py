from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin, TenantModelForm
from apps.outlets.models import Outlet

from .chart import category_accounts
from .models import Account, CashEntry, FundMovement, PaymentAccount


class PaymentAccountForm(TenantModelForm):
    class Meta:
        model = PaymentAccount
        fields = ["name", "kind", "provider", "account_number", "opening_balance", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})


class AccountForm(TenantModelForm):
    """A chart-of-accounts line people add themselves (e.g. a new expense category, a loan, a fixed asset)."""

    class Meta:
        model = Account
        fields = ["code", "name", "type", "description", "is_active"]
        widgets = {"code": forms.TextInput(attrs={"placeholder": "6100", "inputmode": "numeric"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        if self.instance.pk and self.instance.lines.exists():
            # Changing the type of a used account would move its history to another part of the reports.
            self.fields["type"].disabled = True
            self.fields["type"].help_text = _("This account has entries, so its type can't change.")


def _payment_accounts():
    return PaymentAccount.objects.filter(is_active=True)


class CashEntryForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.ModelChoiceField(label=_("Category"), queryset=Account.objects.none())
    payment_account = forms.ModelChoiceField(label=_("Paid from"), queryset=PaymentAccount.objects.none(), empty_label=None)
    amount = forms.DecimalField(label=_("Amount"), max_digits=14, decimal_places=2, min_value=0.01)
    outlet = forms.ModelChoiceField(label=_("Outlet"), queryset=Outlet.objects.none(), required=False, empty_label=_("Whole business"))
    reference = forms.CharField(label=_("Reference"), max_length=100, required=False,
                                widget=forms.TextInput(attrs={"placeholder": _("Bill no., cheque no., transaction ID…")}))
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, kind, outlets=(), **kwargs):
        super().__init__(*args, **kwargs)
        account_type = Account.Type.INCOME if kind == CashEntry.Kind.INCOME else Account.Type.EXPENSE
        self.fields["account"].queryset = category_accounts(account_type).filter(is_active=True).order_by("code")
        self.fields["account"].label_from_instance = lambda a: a.label
        self.fields["payment_account"].queryset = _payment_accounts()
        self.fields["outlet"].queryset = Outlet.objects.filter(pk__in=[o.pk for o in outlets])
        if kind == CashEntry.Kind.INCOME:
            self.fields["payment_account"].label = _("Received in")
        for name in ("account", "payment_account", "outlet"):
            self.fields[name].widget.attrs.update({"data-mp-select": ""})
        self.fields["account"].widget.attrs["data-placeholder"] = _("Pick a category")


class FundMovementForm(BootstrapFormMixin, forms.Form):
    kind = forms.ChoiceField(label=_("What happened"), choices=FundMovement.Kind.choices, widget=forms.RadioSelect,
                             initial=FundMovement.Kind.TRANSFER)
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    from_account = forms.ModelChoiceField(label=_("From"), queryset=PaymentAccount.objects.none(), required=False)
    to_account = forms.ModelChoiceField(label=_("To"), queryset=PaymentAccount.objects.none(), required=False)
    amount = forms.DecimalField(label=_("Amount"), max_digits=14, decimal_places=2, min_value=0.01)
    note = forms.CharField(label=_("Note"), max_length=255, required=False,
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Cash deposited at Dutch-Bangla Bank")}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("from_account", "to_account"):
            self.fields[name].queryset = _payment_accounts()
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": "", "data-placeholder": _("Choose an account")})


class ManualEntryForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    memo = forms.CharField(label=_("Description"), max_length=255,
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Shop furniture bought on loan")}))


class DateRangeForm(forms.Form):
    """?start=…&end=… for the reports; defaults to this month so far."""

    start = forms.DateField(required=False)
    end = forms.DateField(required=False)

    def range(self):
        today = timezone.localdate()
        data = self.cleaned_data if self.is_valid() else {}
        start, end = data.get("start") or today.replace(day=1), data.get("end") or today
        return (start, end) if start <= end else (end, start)
