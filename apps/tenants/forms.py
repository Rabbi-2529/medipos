from django import forms
from django.contrib.auth import password_validation
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.core.forms import BootstrapFormMixin


class SignupForm(BootstrapFormMixin, forms.Form):
    business_name = forms.CharField(label=_("Pharmacy name"), max_length=150)
    business_phone = forms.CharField(label=_("Pharmacy phone"), max_length=20)
    address = forms.CharField(label=_("Address"), required=False, widget=forms.Textarea(attrs={"rows": 2}))
    owner_name = forms.CharField(label=_("Your full name"), max_length=150)
    email = forms.EmailField(label=_("Your email"), help_text=_("You'll log in with this."))
    phone = forms.CharField(
        label=_("Your mobile"), max_length=20, required=False,
        help_text=_("Optional. You can log in with this number too."),
    )
    password1 = forms.CharField(label=_("Password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Confirm password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    plan = forms.ModelChoiceField(
        label=_("Plan"),
        queryset=Plan.objects.filter(is_active=True, is_public=True),
        empty_label=None,
        widget=forms.RadioSelect,
    )
    # Honeypot: hidden from people, bots fill it in.
    website = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        first_plan = self.fields["plan"].queryset.first()
        if first_plan and not self.is_bound:
            self.initial.setdefault("plan", first_plan.pk)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email already exists. Try logging in."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if phone and User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(_("Another account already uses this phone number."))
        return phone

    def clean_website(self):
        if self.cleaned_data["website"]:
            raise forms.ValidationError(_("Please leave this field empty."))
        return ""

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("The two passwords don't match."))
        elif p1:
            probe = User(email=data.get("email", ""), first_name=data.get("owner_name", ""))
            try:
                password_validation.validate_password(p1, probe)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return data
