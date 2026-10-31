from django import forms
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from apps.core.forms import TenantModelForm

from .models import Category, Generic, Manufacturer, Medicine, Rack, Unit
from .services import allocate_codes, clean_name

MAX_IMAGE_BYTES = 1024 * 1024


def _name_picker(url, placeholder):
    """A Select2 box that searches existing names and lets you type a new one."""
    return forms.Select(attrs={
        "data-mp-select": "", "data-ajax-url": url, "data-tags": "", "data-allow-clear": "", "data-placeholder": placeholder,
    })


class MedicineForm(TenantModelForm):
    generic_name = forms.CharField(label=_("Generic"), max_length=150, required=False,
                                   widget=_name_picker(reverse_lazy("items:lookup", args=["generics"]), _("Search or type a generic")))
    manufacturer_name = forms.CharField(label=_("Manufacturer"), max_length=150, required=False,
                                        widget=_name_picker(reverse_lazy("items:lookup", args=["manufacturers"]), _("Search or type a company")))
    category_name = forms.CharField(label=_("Category"), max_length=150, required=False,
                                    widget=_name_picker(reverse_lazy("items:lookup", args=["categories"]), _("Search or type a category")))

    class Meta:
        model = Medicine
        fields = [
            "kind", "name", "strength", "dosage_form", "alternative_name", "prescription_required", "track_expiry",
            "sale_unit", "purchase_unit", "conversion", "strip_size", "alert_quantity", "rack",
            "purchase_price", "sale_price", "wholesale_price", "code", "barcode", "image", "description",
        ]
        widgets = {
            "kind": forms.RadioSelect,
            "description": forms.Textarea(attrs={"rows": 2}),
            "purchase_price": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
            "sale_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "wholesale_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }
        help_texts = {"code": _("Leave blank to number it automatically.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        units = Unit.objects.filter(is_active=True)
        for name in ("sale_unit", "purchase_unit"):
            self.fields[name].queryset = units
            self.fields[name].empty_label = None
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        self.fields["rack"].queryset = Rack.objects.filter(is_active=True)
        self.fields["rack"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": _("No rack")})
        self.fields["dosage_form"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": _("Choose")})
        self.fields["image"].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
        self.fields["kind"].widget.attrs["class"] = "form-check-input"
        if not self.instance.pk and not self.is_bound:
            piece = units.filter(name__iexact="piece").first()
            self.initial.setdefault("sale_unit", piece.pk if piece else None)
            self.initial.setdefault("purchase_unit", piece.pk if piece else None)
        # Name pickers start with the current value as their one option.
        for field, relation in (("generic_name", "generic"), ("manufacturer_name", "manufacturer"), ("category_name", "category")):
            current = self.data.get(self.add_prefix(field)) if self.is_bound else getattr(getattr(self.instance, relation, None), "name", "")
            self.initial[field] = current or ""
            self.fields[field].widget.choices = [("", "")] + ([(current, current)] if current else [])

    def _get_validation_exclusions(self):
        exclude = super()._get_validation_exclusions()
        if not self.cleaned_data.get("code"):
            exclude.add("code")  # numbered in save()
        return exclude

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and getattr(image, "size", 0) > MAX_IMAGE_BYTES:
            raise forms.ValidationError(_("Image must be 1 MB or smaller."))
        return image

    def clean(self):
        data = super().clean()
        if data.get("sale_unit") and data.get("sale_unit") == data.get("purchase_unit"):
            data["conversion"] = 1
            self.instance.conversion = 1
        if data.get("kind") == Medicine.Kind.GENERAL and not data.get("dosage_form"):
            self.instance.dosage_form = ""
        return data

    @staticmethod
    def _master(model, name):
        name = clean_name(name)
        if not name:
            return None
        return model.objects.filter(name__iexact=name).first() or model.objects.create(name=name)

    def save(self, commit=True):
        medicine = super().save(commit=False)
        medicine.generic = self._master(Generic, self.cleaned_data.get("generic_name"))
        medicine.manufacturer = self._master(Manufacturer, self.cleaned_data.get("manufacturer_name"))
        medicine.category = self._master(Category, self.cleaned_data.get("category_name"))
        if not medicine.code:
            medicine.code = allocate_codes(medicine.tenant, 1)[0]
        if commit:
            medicine.save()
        return medicine


class ImportForm(forms.Form):
    file = forms.FileField(label=_("Excel or CSV file"), widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv", "class": "form-control"}))
    update_existing = forms.BooleanField(
        label=_("Update medicines that already exist (matched by Code)"), required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
