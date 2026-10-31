"""Test helpers that build pharmacies the same way the app does."""
from django.utils.text import slugify

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.core.tenancy import tenant_context
from apps.people.models import Employee, Role
from apps.tenants.services import register_pharmacy

PASSWORD = "Strong#Pass2026"


def make_pharmacy(name="Lazz Pharma", plan="business"):
    """A provisioned pharmacy on trial: owner, Main branch outlet, default roles and accounts."""
    slug = slugify(name)
    return register_pharmacy(
        business_name=name, business_phone="01700000000", address="", plan=Plan.objects.get(slug=plan),
        owner_name=f"{name} Owner", email=f"owner@{slug}.test", phone="", password=PASSWORD,
    )


def add_staff(tenant, email, codes=None, outlets=(), all_outlets=False):
    """A non-owner login with its own role holding `codes` (None = no role at all)."""
    with tenant_context(tenant):
        role = Role.objects.create(name=f"Role for {email}", permissions=list(codes)) if codes is not None else None
        user = User.objects.create_user(email, PASSWORD, tenant=tenant)
        employee = Employee.objects.create(user=user, role=role, all_outlets=all_outlets)
        employee.outlets.set(outlets)
    return user
