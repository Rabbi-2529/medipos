from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.billing.models import Plan, SubscriptionPayment
from apps.core.forms import BootstrapFormMixin
from apps.tenants.forms import SignupForm

MONTH_CHOICES = [(1, _("1 month")), (3, _("3 months")), (6, _("6 months")), (12, _("12 months"))]


def _select2(field, searchable=False):
    field.widget.attrs["data-mp-select"] = ""
    if not searchable:
        field.widget.attrs["data-no-search"] = ""


class AddPharmacyForm(SignupForm):
    """Sign-up done by ThingBD staff for a customer: any active plan, chosen trial length, no honeypot."""

    trial_days = forms.IntegerField(
        label=_("Trial days"), min_value=1, max_value=365, initial=settings.TRIAL_DAYS,
        help_text=_("Record a payment afterwards to make it a paid account."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["website"]
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True)
        self.fields["plan"].widget = forms.Select(
            attrs={"class": "form-select", "data-mp-select": "", "data-no-search": ""},
            choices=self.fields["plan"].choices,
        )
        self.fields["trial_days"].widget.attrs["class"] = "form-control"
        self.fields["owner_name"].label = _("Owner's full name")
        self.fields["email"].label = _("Owner's email")
        self.fields["email"].help_text = _("The owner logs in with this.")
        self.fields["phone"].label = _("Owner's mobile")
        self.fields["password1"].label = _("Owner's password")
        self.fields["password1"].help_text = _("Share it with the owner. They can change it from their profile.")


class PaymentForm(BootstrapFormMixin, forms.Form):
    plan = forms.ModelChoiceField(label=_("Plan"), queryset=Plan.objects.filter(is_active=True))
    months = forms.TypedChoiceField(label=_("Period"), choices=MONTH_CHOICES, coerce=int, initial=1)
    amount = forms.DecimalField(
        label=_("Amount (৳)"), max_digits=12, decimal_places=2, min_value=0, required=False,
        help_text=_("Leave empty to charge the plan price."),
    )
    method = forms.ChoiceField(label=_("Method"), choices=SubscriptionPayment.Method.choices)
    reference = forms.CharField(label=_("Reference"), max_length=100, required=False)
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("plan", "months", "method"):
            _select2(self.fields[name])


class ChangePlanForm(BootstrapFormMixin, forms.Form):
    plan = forms.ModelChoiceField(label=_("New plan"), queryset=Plan.objects.filter(is_active=True))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _select2(self.fields["plan"])


class ExtendTrialForm(BootstrapFormMixin, forms.Form):
    days = forms.IntegerField(label=_("Extra days"), min_value=1, max_value=90, initial=7)


class SuspendForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(label=_("Reason"), max_length=255, help_text=_("Shown to the pharmacy."))


class PlanForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            "name", "slug", "description", "price_monthly", "price_yearly",
            "max_outlets", "max_users", "max_medicines", "sort_order", "is_public", "is_active",
        ]
        help_texts = {
            "max_outlets": _("Empty = unlimited."),
            "max_users": _("Empty = unlimited."),
            "max_medicines": _("Empty = unlimited."),
        }
