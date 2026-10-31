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


def make_medicine(tenant, name="Napa", *, conversion=10, track_expiry=True, purchase_price="1.00", sale_price="1.20", **extra):
    """A medicine sold by Piece and bought by Box of `conversion` (or by Piece when conversion is 1)."""
    from decimal import Decimal

    from apps.items.models import Medicine, Unit

    with tenant_context(tenant):
        piece, box = Unit.objects.get(name="Piece"), Unit.objects.get(name="Box")
        return Medicine.objects.create(
            name=name, code=extra.pop("code", name.upper()[:10]), sale_unit=piece, purchase_unit=box if conversion > 1 else piece,
            conversion=conversion, track_expiry=track_expiry, purchase_price=Decimal(purchase_price), sale_price=Decimal(sale_price), **extra,
        )


def make_supplier(tenant, name="Square Distribution", **fields):
    from apps.suppliers.models import Supplier

    with tenant_context(tenant):
        return Supplier.objects.create(name=name, **fields)


def add_staff(tenant, email, codes=None, outlets=(), all_outlets=False):
    """A non-owner login with its own role holding `codes` (None = no role at all)."""
    with tenant_context(tenant):
        role = Role.objects.create(name=f"Role for {email}", permissions=list(codes)) if codes is not None else None
        user = User.objects.create_user(email, PASSWORD, tenant=tenant)
        employee = Employee.objects.create(user=user, role=role, all_outlets=all_outlets)
        employee.outlets.set(outlets)
    return user
