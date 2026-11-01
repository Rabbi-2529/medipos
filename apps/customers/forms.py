from django import forms
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounting.models import PaymentAccount
from apps.core.forms import BootstrapFormMixin, TenantModelForm

from .models import Customer


class CustomerForm(TenantModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "credit_limit", "default_discount", "opening_balance", "notes", "no_marketing"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class ReceiptForm(BootstrapFormMixin, forms.Form):
    customer = forms.ModelChoiceField(label=_("Customer"), queryset=Customer.objects.none())
    sale = forms.ModelChoiceField(label=_("Against sale"), queryset=None, required=False,
                                  help_text=_("Optional. Leave empty to pay toward their whole due."))
    amount = forms.DecimalField(label=_("Amount"), max_digits=14, decimal_places=2, min_value=0.01)
    account = forms.ModelChoiceField(label=_("Received in"), queryset=PaymentAccount.objects.none(), empty_label=None)
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(label=_("Reference"), max_length=100, required=False)
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        from apps.sales.models import Sale

        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["sale"].queryset = Sale.objects.exclude(customer=None)
        self.fields["account"].queryset = PaymentAccount.objects.filter(is_active=True)
        self.initial.setdefault("date", timezone.localdate())
        self.fields["customer"].widget.attrs.update({"data-mp-select": "", "data-placeholder": _("Search name or phone"),
                                                      "data-ajax-url": reverse_lazy("customers:lookup")})
        self.fields["sale"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": _("Whole due")})
        self.fields["account"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        chosen = self.data.get("customer") or self.initial.get("customer")
        chosen = getattr(chosen, "pk", chosen)
        customer = self.fields["customer"].queryset.filter(pk=chosen).first() if chosen and str(chosen).isdigit() else None
        self.fields["customer"].widget.choices = [("", "")] + ([(customer.pk, str(customer))] if customer else [])
        sales = Sale.objects.filter(customer=customer).order_by("-sold_at")[:100] if customer else []
        self.fields["sale"].widget.choices = [("", "")] + [(s.pk, f"{s.number} · {s.sold_at:%d %b %Y}") for s in sales]
