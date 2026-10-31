from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounting.models import PaymentAccount
from apps.core.forms import BootstrapFormMixin, TenantModelForm
from apps.people.models import Employee

from .models import OfficeNetwork, Shift
from .services import HRError, active_staff, clean_network


def _payment_accounts():
    return PaymentAccount.objects.filter(is_active=True)


class AdvanceForm(BootstrapFormMixin, forms.Form):
    employee = forms.ModelChoiceField(label=_("Employee"), queryset=Employee.objects.none())
    amount = forms.DecimalField(label=_("Amount"), max_digits=12, decimal_places=2, min_value=0.01)
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    payment_account = forms.ModelChoiceField(label=_("Paid from"), queryset=PaymentAccount.objects.none(), empty_label=None)
    note = forms.CharField(label=_("Note"), max_length=255, required=False,
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Eid advance")}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(pk__in=[e.pk for e in active_staff()])
        self.fields["employee"].label_from_instance = lambda e: e.display_name
        self.fields["payment_account"].queryset = _payment_accounts()
        self.fields["employee"].widget.attrs.update({"data-mp-select": "", "data-placeholder": _("Pick a person")})
        self.fields["payment_account"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})


class PrepareForm(BootstrapFormMixin, forms.Form):
    month = forms.DateField(label=_("Month"), input_formats=["%Y-%m"], widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"))


class PayForm(BootstrapFormMixin, forms.Form):
    payment_account = forms.ModelChoiceField(label=_("Pay from"), queryset=PaymentAccount.objects.none(), empty_label=None)
    date = forms.DateField(label=_("Payment date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_account"].queryset = _payment_accounts()
        self.fields["payment_account"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})


class ShiftForm(TenantModelForm):
    class Meta:
        model = Shift
        fields = ["name", "start", "end", "grace_minutes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("e.g. Morning")}),
            "start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }
        help_texts = {"grace_minutes": _("Checking in later than this after the start counts as late.")}


class NetworkForm(TenantModelForm):
    class Meta:
        model = OfficeNetwork
        fields = ["address", "label"]
        widgets = {
            "address": forms.TextInput(attrs={"placeholder": "103.4.5.6", "spellcheck": "false", "autocomplete": "off"}),
            "label": forms.TextInput(attrs={"placeholder": _("e.g. Main branch Wi-Fi")}),
        }
        help_texts = {"address": _("One address, or a range like 103.4.5.0/24.")}

    def clean_address(self):
        try:
            address = clean_network(self.cleaned_data["address"])
        except HRError as error:
            raise forms.ValidationError(str(error))
        if OfficeNetwork.objects.filter(address=address).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("This address is already on the list."))
        return address
