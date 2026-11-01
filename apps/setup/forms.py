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
            "vat_percent", "round_total", "default_language",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("currency_position", "date_format", "default_language"):
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


MAX_LETTERHEAD_BYTES = 2 * 1024 * 1024
SHOW_FIELDS = ("show_logo", "show_address", "show_license", "show_cashier", "show_customer_phone", "show_generic", "show_batch",
               "show_customer_due", "show_points")


class InvoiceDesignForm(TenantModelForm):
    """What invoices look like, the language they print in, and how the POS prints."""

    class Meta:
        model = BusinessSettings
        fields = [
            "invoice_title", "receipt_language", "receipt_bangla_digits", "receipt_size", "receipt_font", *SHOW_FIELDS, "letterhead",
            "invoice_footer", "thank_you", "invoice_terms", "qr_code", "qr_text", "auto_print", "pos_default_account",
        ]
        widgets = {"invoice_footer": forms.Textarea(attrs={"rows": 2}), "invoice_terms": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounting.models import PaymentAccount

        account = self.fields["pos_default_account"]
        account.queryset = PaymentAccount.objects.filter(is_active=True)
        account.empty_label = _("Cash")
        for name in ("receipt_language", "receipt_size", "receipt_font", "qr_code", "pos_default_account"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        self.fields["letterhead"].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"

    def clean_letterhead(self):
        image = self.cleaned_data.get("letterhead")
        if image and getattr(image, "size", 0) > MAX_LETTERHEAD_BYTES:
            raise forms.ValidationError(_("The letterhead must be 2 MB or smaller."))
        return image

    def clean(self):
        data = super().clean()
        if data.get("qr_code") == "text" and not (data.get("qr_text") or "").strip():
            self.add_error("qr_text", _("Write what the QR code should hold."))
        return data
