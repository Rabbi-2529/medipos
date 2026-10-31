from django import forms
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounting.models import PaymentAccount
from apps.core.forms import BootstrapFormMixin
from apps.outlets.models import Outlet
from apps.suppliers.models import Supplier


class PurchaseForm(BootstrapFormMixin, forms.Form):
    """Header + payment of a purchase. The lines arrive as JSON in `lines_json` (built by the page's script)."""

    supplier = forms.ModelChoiceField(label=_("Supplier"), queryset=Supplier.objects.none())
    outlet = forms.ModelChoiceField(label=_("Receive at"), queryset=Outlet.objects.none(), empty_label=None)
    date = forms.DateField(label=_("Purchase date"), widget=forms.DateInput(attrs={"type": "date"}))
    supplier_invoice_no = forms.CharField(label=_("Supplier invoice no."), max_length=50, required=False)
    discount = forms.DecimalField(label=_("Discount"), max_digits=14, decimal_places=2, min_value=0, required=False)
    update_prices = forms.BooleanField(label=_("Update medicine MRP and cost from this purchase"), required=False)
    note = forms.CharField(label=_("Note"), max_length=255, required=False)
    paid_now = forms.DecimalField(label=_("Paid now"), max_digits=14, decimal_places=2, min_value=0, required=False)
    account = forms.ModelChoiceField(label=_("Paid from"), queryset=PaymentAccount.objects.none(), required=False, empty_label=None)
    lines_json = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, allowed_outlets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        self.fields["outlet"].queryset = Outlet.objects.filter(pk__in=[o.pk for o in allowed_outlets])
        self.fields["account"].queryset = PaymentAccount.objects.filter(is_active=True)
        self.initial.setdefault("date", timezone.localdate())
        self.initial.setdefault("update_prices", True)
        self.fields["supplier"].widget.attrs.update({
            "data-mp-select": "", "data-placeholder": _("Search supplier"), "data-ajax-url": reverse_lazy("suppliers:lookup"),
        })
        for name in ("outlet", "account"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        chosen = self.data.get("supplier") or self.initial.get("supplier")
        chosen = getattr(chosen, "pk", chosen)
        supplier = self.fields["supplier"].queryset.filter(pk=chosen).first() if chosen and str(chosen).isdigit() else None
        self.fields["supplier"].widget.choices = [("", "")] + ([(supplier.pk, supplier.name)] if supplier else [])

    def clean(self):
        data = super().clean()
        if (data.get("paid_now") or 0) > 0 and not data.get("account"):
            self.add_error("account", _("Choose where the money was paid from."))
        return data


class ReturnForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(label=_("Return date"), widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(label=_("Reason"), max_length=255, required=False, help_text=_("e.g. near expiry, damaged in transit"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("date", timezone.localdate())
