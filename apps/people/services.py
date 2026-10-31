from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.billing.limits import check_limit

from .models import Employee

PROFILE_FIELDS = ("designation", "joined_on", "nid", "address")


class EmployeeError(Exception):
    """A user-facing reason an employee change was refused."""


def _apply_access(employee, data):
    if "role" in data:
        employee.role = data["role"]
        employee.all_outlets = data["all_outlets"]


@transaction.atomic
def create_employee(tenant, data):
    """New login + staff profile. Counts toward the plan's user limit."""
    check_limit(tenant, "users")
    user = User.objects.create_user(
        email=data["email"], password=data["password1"], phone=data["phone"],
        first_name=data["first_name"], last_name=data["last_name"], tenant=tenant,
    )
    employee = Employee(user=user, **{f: data[f] for f in PROFILE_FIELDS})
    _apply_access(employee, data)
    employee.save()
    employee.outlets.set([] if data["all_outlets"] else data["outlets"])
    return employee


def _guard(employee, actor):
    if employee.user.is_owner and not actor.is_owner:
        raise EmployeeError(_("Only the owner can change the owner's account."))


@transaction.atomic
def update_employee(employee, data, *, actor):
    _guard(employee, actor)
    user = employee.user
    for field in ("first_name", "last_name", "email", "phone"):
        setattr(user, field, data[field])
    user.save(update_fields=["first_name", "last_name", "email", "phone"])
    for field in PROFILE_FIELDS:
        setattr(employee, field, data[field])
    _apply_access(employee, data)
    employee.save()
    if "outlets" in data:
        employee.outlets.set([] if data["all_outlets"] else data["outlets"])
    return employee


@transaction.atomic
def set_active(employee, active, *, actor):
    user = employee.user
    if not active:
        if user.is_owner:
            raise EmployeeError(_("The owner's account can't be deactivated."))
        if user.pk == actor.pk:
            raise EmployeeError(_("You can't deactivate your own account."))
    elif not user.is_active:
        check_limit(employee.tenant, "users")
    user.is_active = active
    user.save(update_fields=["is_active"])
    return employee


def set_password(employee, password, *, actor):
    _guard(employee, actor)
    employee.user.set_password(password)
    employee.user.save(update_fields=["password"])
