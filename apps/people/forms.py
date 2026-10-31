from django import forms
from django.contrib.auth import password_validation
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.forms import BootstrapFormMixin, TenantModelForm
from apps.core.permissions import ALL_CODES
from apps.outlets.models import Outlet

from .models import Role


class EmployeeForm(BootstrapFormMixin, forms.Form):
    """One form for the login (User) and the staff profile (Employee)."""

    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    email = forms.EmailField(label=_("Email"), help_text=_("Used to log in."))
    phone = forms.CharField(label=_("Mobile"), max_length=20, required=False, help_text=_("Can also be used to log in."))
    password1 = forms.CharField(label=_("Password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Confirm password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    role = forms.ModelChoiceField(label=_("Role"), queryset=Role.objects.none(), required=False, empty_label=_("No role (no access)"))
    designation = forms.CharField(label=_("Designation"), max_length=60, required=False)
    all_outlets = forms.BooleanField(label=_("Can work in all outlets"), required=False)
    outlets = forms.ModelMultipleChoiceField(label=_("Outlets"), queryset=Outlet.objects.none(), required=False)
    joined_on = forms.DateField(label=_("Joined on"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    nid = forms.CharField(label=_("NID no."), max_length=30, required=False)
    address = forms.CharField(label=_("Address"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        if employee is not None and "initial" not in kwargs:
            user = employee.user
            kwargs["initial"] = {
                "first_name": user.first_name, "last_name": user.last_name, "email": user.email, "phone": user.phone,
                "role": employee.role_id, "designation": employee.designation, "all_outlets": employee.all_outlets,
                "outlets": list(employee.outlets.values_list("pk", flat=True)), "joined_on": employee.joined_on,
                "nid": employee.nid, "address": employee.address,
            }
        super().__init__(*args, **kwargs)
        # Querysets are set here, not on the class: at import time no tenant is active.
        self.fields["role"].queryset = Role.objects.all()
        self.fields["outlets"].queryset = Outlet.objects.filter(is_active=True)
        self.fields["role"].widget.attrs.update({"data-mp-select": "", "data-allow-clear": "", "data-placeholder": _("No role (no access)")})
        self.fields["outlets"].widget.attrs.update({"data-mp-select": "", "data-placeholder": _("Pick outlets")})
        if employee is not None:
            del self.fields["password1"], self.fields["password2"]
            if employee.user.is_owner:
                # The owner always has full access everywhere.
                for name in ("role", "all_outlets", "outlets"):
                    del self.fields[name]

    @property
    def user_pk(self):
        return self.employee.user_id if self.employee else None

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user_pk).exists():
            raise forms.ValidationError(_("Another account already uses this email."))
        return email

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if phone and User.objects.filter(phone=phone).exclude(pk=self.user_pk).exists():
            raise forms.ValidationError(_("Another account already uses this phone number."))
        return phone

    def clean(self):
        data = super().clean()
        if "outlets" in self.fields and not data.get("all_outlets") and not data.get("outlets"):
            self.add_error("outlets", _("Pick at least one outlet, or allow all outlets."))
        if "password1" in self.fields:
            p1, p2 = data.get("password1"), data.get("password2")
            if p1 and p2 and p1 != p2:
                self.add_error("password2", _("The two passwords don't match."))
            elif p1:
                try:
                    password_validation.validate_password(p1, User(email=data.get("email", ""), first_name=data.get("first_name", "")))
                except forms.ValidationError as error:
                    self.add_error("password1", error)
        return data


class SetPasswordForm(BootstrapFormMixin, forms.Form):
    password1 = forms.CharField(label=_("New password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Confirm password"), strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("The two passwords don't match."))
        elif p1:
            try:
                password_validation.validate_password(p1, self.user)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return data


class RoleForm(TenantModelForm):
    perms = forms.MultipleChoiceField(
        choices=[(code, code) for code in sorted(ALL_CODES)], required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Role
        fields = ["name", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["perms"] = sorted(self.instance.codes)

    @property
    def selected_codes(self):
        if self.is_bound:
            return set(self.data.getlist("perms"))
        return set(self.initial.get("perms", []))

    def save(self, commit=True):
        role = super().save(commit=False)
        # Keep stored codes for modules that don't exist yet; replace the ones this screen shows.
        kept = set(role.permissions or []) - ALL_CODES
        role.permissions = sorted(kept | set(self.cleaned_data["perms"]))
        if commit:
            role.save()
        return role
