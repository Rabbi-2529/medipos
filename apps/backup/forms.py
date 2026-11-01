from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .exports import DATASETS


class ExcelExportForm(BootstrapFormMixin, forms.Form):
    datasets = forms.MultipleChoiceField(label=_("Lists"), choices=[(d.key, d.label) for d in DATASETS], widget=forms.CheckboxSelectMultiple,
                                         error_messages={"required": _("Tick at least one list.")})
    start = forms.DateField(label=_("From"), widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label=_("To"), widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        today = timezone.localdate()
        kwargs.setdefault("initial", {"start": today.replace(month=1, day=1), "end": today,
                                      "datasets": ["medicines", "customers", "sales", "purchases"]})
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            data["start"], data["end"] = data["end"], data["start"]
        return data
