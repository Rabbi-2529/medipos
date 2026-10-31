from django import forms
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounting.models import PaymentAccount
from apps.core.forms import BootstrapFormMixin, TenantModelForm

from .models import Supplier


class SupplierForm(TenantModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "opening_balance"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


def account_field():
    return forms.ModelChoiceField(label=_("Paid from"), queryset=PaymentAccount.objects.none(), empty_label=None)


class PaymentForm(BootstrapFormMixin, forms.Form):
    supplier = forms.ModelChoiceField(label=_("Supplier"), queryset=Supplier.objects.none())
    purchase = forms.ModelChoiceField(label=_("Against purchase"), queryset=None, required=False,
                                      help_text=_("Optional. Leave empty for a general payment on account."))
    amount = forms.DecimalField(label=_("Amount"), max_digits=14, decimal_places=2, min_value=0.01)
    account = account_field()
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(label=_("Reference"), max_length=100, required=False,
                                help_text=_("Cheque no., bKash transaction ID…"))
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        from apps.purchases.models import Purchase

        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        self.fields["purchase"].queryset = Purchase.objects.filter(status=Purchase.Status.RECEIVED)
        self.fields["account"].queryset = PaymentAccount.objects.filter(is_active=True)
        self.initial.setdefault("date", timezone.localdate())
        self.fields["supplier"].widget.attrs.update({
            "data-mp-select": "", "data-placeholder": _("Choose supplier"),
            "data-ajax-url": reverse_lazy("suppliers:lookup"),
        })
        self.fields["purchase"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": _("General payment")})
        self.fields["account"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        # Only render the chosen supplier/purchase as options; the rest load by search.
        chosen = self.data.get("supplier") or self.initial.get("supplier")
        supplier = self.fields["supplier"].queryset.filter(pk=getattr(chosen, "pk", chosen)).first() if chosen else None
        self.fields["supplier"].widget.choices = [("", "")] + ([(supplier.pk, supplier.name)] if supplier else [])
        dues = self.fields["purchase"].queryset.filter(supplier=supplier) if supplier else Purchase.objects.none()
        self.fields["purchase"].widget.choices = [("", "")] + [(p.pk, f"{p.number} · {p.date:%d %b %Y}") for p in dues[:100]]
