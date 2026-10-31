from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

User = get_user_model()


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    username = forms.CharField(
        label=_("Email or phone"),
        max_length=254,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    remember_me = forms.BooleanField(label=_("Remember me"), required=False, initial=True)

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Wrong email/phone or password."),
    }


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "language"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"].choices = [("", _("Same as the pharmacy"))] + list(settings.LANGUAGES)

    def clean_phone(self):
        # Phone is a login identifier, so it must point at exactly one account.
        phone = self.cleaned_data["phone"].strip()
        if phone and User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("Another account already uses this phone number."))
        return phone


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class StyledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    pass


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass
