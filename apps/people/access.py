"""Who may do what, and in which outlets.

Owners have every permission and every active outlet. Other staff get their
role's codes and the outlets on their employee profile.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from apps.core.permissions import ALL_CODES
from apps.outlets.models import Outlet

from .models import Employee


def get_employee(user):
    if not hasattr(user, "_employee_cache"):
        user._employee_cache = (
            Employee.all_objects.select_related("role").filter(user=user).first() if user.tenant_id else None
        )
    return user._employee_cache


def user_permissions(user):
    if not getattr(user, "is_authenticated", False) or user.tenant_id is None:
        return frozenset()
    cached = getattr(user, "_perm_cache", None)
    if cached is None:
        if user.is_owner:
            cached = ALL_CODES
        else:
            employee = get_employee(user)
            cached = employee.role.codes if employee and employee.role_id else frozenset()
        user._perm_cache = cached
    return cached


def has_perm(user, code):
    return code in user_permissions(user)


def allowed_outlets(user):
    """Active outlets this user may enter, by code."""
    outlets = Outlet.all_objects.filter(tenant_id=user.tenant_id, is_active=True)
    if user.tenant_id is None:
        return outlets.none()
    if user.is_owner:
        return outlets
    employee = get_employee(user)
    if employee is None:
        return outlets.none()
    return outlets if employee.all_outlets else outlets.filter(employees=employee)


class Can:
    """Template helper: {% if can.outlet.create %} … {% endif %}."""

    def __init__(self, codes):
        self._codes = codes

    def __getitem__(self, module):
        return _ModuleCan(self._codes, module)


class _ModuleCan:
    def __init__(self, codes, module):
        self._codes, self._module = codes, module

    def __getitem__(self, action):
        return f"{self._module}.{action}" in self._codes


def _check(request, code):
    if getattr(request, "tenant", None) is None or not has_perm(request.user, code):
        raise PermissionDenied


def shop_permission_required(code):
    """For pharmacy screens: logged in, belongs to a pharmacy, and holds `code`."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            _check(request, code)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class ShopPermissionMixin:
    """Class-based version of shop_permission_required; set `permission`."""

    permission = None

    def get_permission(self):
        return self.permission

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        _check(request, self.get_permission())
        return super().dispatch(request, *args, **kwargs)
