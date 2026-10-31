from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin, TenantModelForm
from apps.tenants.models import Tenant

from .models import BusinessSettings, Sequence

MAX_LOGO_BYTES = 1024 * 1024


class BusinessProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ["name", "phone", "email", "address"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class BusinessSettingsForm(TenantModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            "logo", "drug_license_no", "vat_number",
            "currency_symbol", "currency_code", "currency_position", "decimal_places", "date_format",
            "invoice_footer", "vat_percent", "round_total", "receipt_size", "default_language", "receipt_bangla_digits",
        ]
        widgets = {"invoice_footer": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("currency_position", "date_format", "receipt_size", "default_language"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        self.fields["logo"].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and getattr(logo, "size", 0) > MAX_LOGO_BYTES:
            raise forms.ValidationError(_("Logo must be 1 MB or smaller."))
        return logo


class InvoiceNumberingForm(TenantModelForm):
    class Meta:
        model = Sequence
        fields = ["prefix", "next_value", "padding"]
        labels = {"prefix": _("Invoice prefix"), "next_value": _("Next invoice number"), "padding": _("Digits")}

    def clean_next_value(self):
        value = self.cleaned_data["next_value"]
        if value < 1:
            raise forms.ValidationError(_("Must be 1 or more."))
        return value
