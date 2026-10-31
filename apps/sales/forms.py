from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.forms import TenantModelForm
from apps.items.choices import DosageForm
from apps.items.models import Medicine

from .models import Promotion


class PromotionForm(TenantModelForm):
    dosage_form = forms.ChoiceField(label=_("Dosage form"), choices=[("", "—"), *DosageForm.choices], required=False)

    class Meta:
        model = Promotion
        fields = ["name", "kind", "value", "scope", "category", "dosage_form", "medicines", "min_quantity", "starts_on", "ends_on", "is_active"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
            "name": forms.TextInput(attrs={"placeholder": _("e.g. Winter syrup offer")}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": "—"})
        self.fields["dosage_form"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        # Medicines are searched as you type; only the chosen ones are written into the page.
        medicines = self.fields["medicines"]
        medicines.queryset = Medicine.objects.filter(is_active=True)
        chosen = self.data.getlist(self.add_prefix("medicines")) if self.is_bound else (
            [m.pk for m in self.instance.medicines.all()] if self.instance.pk else [])
        medicines.widget.choices = [(m.pk, m.display_name) for m in Medicine.objects.filter(pk__in=[c for c in chosen if str(c).isdigit()])]
        medicines.widget.attrs.update({"data-mp-select": "", "data-ajax-url": reverse("items:lookup", args=["medicines"]),
                                       "data-placeholder": _("Search medicines to add")})
        for name in ("kind", "scope"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})

    def clean(self):
        data = super().clean()
        value, kind, scope = data.get("value"), data.get("kind"), data.get("scope")
        if value is not None and value <= 0:
            self.add_error("value", _("Enter a discount above zero."))
        elif value is not None and kind == Promotion.Kind.PERCENT and value > 100:
            self.add_error("value", _("A percentage can't be more than 100."))
        needed = {Promotion.Scope.CATEGORY: "category", Promotion.Scope.FORM: "dosage_form", Promotion.Scope.MEDICINES: "medicines"}
        field = needed.get(scope)
        if field and not data.get(field):
            self.add_error(field, _("Choose what this promotion applies to."))
        # Keep only the target that matches "applies to".
        if scope != Promotion.Scope.CATEGORY:
            data["category"] = None
        if scope != Promotion.Scope.FORM:
            data["dosage_form"] = ""
        if scope != Promotion.Scope.MEDICINES:
            data["medicines"] = Medicine.objects.none()
        if data.get("starts_on") and data.get("ends_on") and data["ends_on"] < data["starts_on"]:
            self.add_error("ends_on", _("The end date is before the start date."))
        return data
