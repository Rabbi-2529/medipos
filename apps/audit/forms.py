from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin
from apps.outlets.models import Outlet

from .models import AuditFinding, BranchAudit


class StartAuditForm(BootstrapFormMixin, forms.Form):
    outlet = forms.ModelChoiceField(label=_("Branch"), queryset=Outlet.objects.none(), empty_label=None)
    start = forms.DateField(label=_("From"), widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label=_("To"), widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label=_("Note"), max_length=255, required=False, help_text=_("e.g. Surprise visit, quarterly audit."))

    def __init__(self, *args, **kwargs):
        today = timezone.localdate()
        last_month_end = today.replace(day=1) - timedelta(days=1)
        kwargs.setdefault("initial", {"start": last_month_end.replace(day=1), "end": last_month_end})
        super().__init__(*args, **kwargs)
        self.fields["outlet"].queryset = Outlet.objects.filter(is_active=True).order_by("code")
        self.fields["outlet"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})


class FindingForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = AuditFinding
        fields = ["title", "severity", "detail", "action"]
        widgets = {"detail": forms.Textarea(attrs={"rows": 2})}


class SignOffForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = BranchAudit
        fields = ["verdict", "summary"]
        widgets = {"summary": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["verdict"].required = True
        self.fields["verdict"].choices = [("", "—"), *BranchAudit.Verdict.choices]  # the auditor picks it, nothing preselected
