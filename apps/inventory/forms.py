from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin
from apps.outlets.models import Outlet

from .models import WriteOff


class _OutletForm(BootstrapFormMixin, forms.Form):
    """The outlet list is the user's own outlets, set per request (querysets on the class would be empty)."""

    def __init__(self, *args, outlets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["outlet"].queryset = Outlet.objects.filter(pk__in=[o.pk for o in outlets])
        if len(outlets) == 1:
            self.fields["outlet"].initial = outlets[0].pk


class WriteOffForm(_OutletForm):
    outlet = forms.ModelChoiceField(label=_("Outlet"), queryset=Outlet.objects.none(), empty_label=None)
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.ChoiceField(label=_("Reason"), choices=WriteOff.Reason.choices)
    note = forms.CharField(label=_("Note"), max_length=255, required=False)


class StockCountForm(_OutletForm):
    outlet = forms.ModelChoiceField(label=_("Outlet"), queryset=Outlet.objects.none(), empty_label=None)
    date = forms.DateField(label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label=_("Note"), max_length=255, required=False,
                           widget=forms.TextInput(attrs={"placeholder": _("e.g. Monthly count, rack A")}))


class TransferForm(_OutletForm):
    outlet = forms.ModelChoiceField(label=_("Send from"), queryset=Outlet.objects.none(), empty_label=None)
    to_outlet = forms.ModelChoiceField(label=_("Send to"), queryset=Outlet.objects.none())
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Any active outlet of the pharmacy can receive, even one this user can't enter.
        self.fields["to_outlet"].queryset = Outlet.objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        if data.get("outlet") and data.get("to_outlet") and data["outlet"] == data["to_outlet"]:
            self.add_error("to_outlet", _("Pick a different outlet to send to."))
        return data
