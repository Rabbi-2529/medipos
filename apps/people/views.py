from django.contrib import messages
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.html import escape, format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.billing.limits import PlanLimitReached, usage_summary
from apps.core.datatables import datatable_json
from apps.core.permissions import ACTION_LABELS, CRUD, MODULES

from .access import has_perm, shop_permission_required
from .forms import EmployeeForm, RoleForm, SetPasswordForm
from .models import Employee, Role
from .services import EmployeeError, create_employee, set_active, set_password, update_employee

# ---- Employees -----------------------------------------------------------------


@shop_permission_required("employee.list")
def employee_list(request):
    users = next(row for row in usage_summary(request.tenant) if row["key"] == "users")
    return render(request, "people/employee_list.html", {"user_usage": users})


def _employee_row(request, employee):
    user = employee.user
    edit_url = reverse("people:employee_edit", args=[employee.pk])
    name = user.get_full_name() or user.email
    if user.is_owner:
        role = format_html('<span class="badge text-bg-primary">{}</span>', _("Owner"))
    else:
        role = escape(employee.role.name) if employee.role else format_html('<span class="text-body-secondary">{}</span>', _("No role"))
    if user.is_owner or employee.all_outlets:
        outlets = _("All outlets")
    else:
        outlets = ", ".join(o.name for o in employee.outlets.all()) or "—"
    status = format_html('<span class="state-badge state-{}">{}</span>', "active" if user.is_active else "suspended",
                         _("Active") if user.is_active else _("Inactive"))
    action = format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>', edit_url, _("Edit")) \
        if has_perm(request.user, "employee.edit") else ""
    return [
        format_html('<a class="fw-semibold" href="{}">{}</a><small class="d-block text-body-secondary">{}</small>',
                    edit_url, name, employee.designation or ""),
        format_html('{}<small class="d-block text-body-secondary">{}</small>', user.email, user.phone or ""),
        role,
        escape(outlets),
        status,
        date_format(timezone.localtime(user.last_login), "d M Y, h:i A") if user.last_login else "—",
        action,
    ]


@shop_permission_required("employee.list")
def employee_data(request):
    queryset = Employee.objects.select_related("user", "role").prefetch_related("outlets")
    status = request.GET.get("status")
    if status in ("active", "inactive"):
        queryset = queryset.filter(user__is_active=status == "active")

    def search(qs, term):
        return qs.filter(
            Q(user__first_name__icontains=term) | Q(user__last_name__icontains=term) | Q(user__email__icontains=term)
            | Q(user__phone__icontains=term) | Q(designation__icontains=term) | Q(role__name__icontains=term)
        )

    return datatable_json(
        request, queryset.order_by("-user__is_owner", "user__first_name"),
        order_columns=["user__first_name", "user__email", "role__name", None, "user__is_active", "user__last_login", None],
        row=lambda e: _employee_row(request, e),
        search=search,
    )


@shop_permission_required("employee.create")
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            employee = create_employee(request.tenant, form.cleaned_data)
        except PlanLimitReached as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("%(name)s can now log in with %(email)s.") % {
                "name": employee.user.get_full_name() or employee.user.email, "email": employee.user.email,
            })
            return redirect("people:employees")
    return render(request, "people/employee_form.html", {"form": form, "title": _("Add employee")})


@shop_permission_required("employee.edit")
def employee_edit(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("user", "role"), pk=pk)
    form = EmployeeForm(request.POST or None, employee=employee)
    if request.method == "POST" and form.is_valid():
        try:
            update_employee(employee, form.cleaned_data, actor=request.user)
        except EmployeeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Changes saved."))
            return redirect("people:employees")
    return render(request, "people/employee_form.html", {
        "form": form,
        "employee": employee,
        "password_form": SetPasswordForm(user=employee.user),
        "title": employee.user.get_full_name() or employee.user.email,
        "can_manage": not employee.user.is_owner or request.user.is_owner,
    })


@shop_permission_required("employee.delete")
@require_POST
def employee_toggle(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("user"), pk=pk)
    activate = not employee.user.is_active
    try:
        set_active(employee, activate, actor=request.user)
    except (EmployeeError, PlanLimitReached) as error:
        messages.error(request, str(error))
    else:
        name = employee.user.get_full_name() or employee.user.email
        messages.success(request, (_("%(name)s can log in again.") if activate else _("%(name)s can no longer log in.")) % {"name": name})
    return redirect("people:employee_edit", pk=pk)


@shop_permission_required("employee.edit")
@require_POST
def employee_password(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("user"), pk=pk)
    form = SetPasswordForm(request.POST, user=employee.user)
    if not form.is_valid():
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    else:
        try:
            set_password(employee, form.cleaned_data["password1"], actor=request.user)
        except EmployeeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Password changed. Share the new password with them."))
    return redirect("people:employee_edit", pk=pk)


# ---- Roles ---------------------------------------------------------------------


@shop_permission_required("role.list")
def role_list(request):
    roles = Role.objects.annotate(employee_count=Count("employees"))
    return render(request, "people/role_list.html", {"roles": roles})


def _permission_grid(selected):
    """Rows of the roles screen, grouped like the sidebar."""
    groups = {}
    for module in MODULES:
        cells = [
            {"code": f"{module.key}.{action}", "checked": f"{module.key}.{action}" in selected} if action in module.actions else None
            for action in CRUD
        ]
        groups.setdefault(str(module.group), []).append({"module": module, "cells": cells})
    return groups


def _role_form(request, role=None):
    form = RoleForm(request.POST or None, instance=role)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Role saved."))
        return redirect("people:roles")
    return render(request, "people/role_form.html", {
        "form": form,
        "role": role,
        "grid": _permission_grid(form.selected_codes),
        "actions": [ACTION_LABELS[a] for a in CRUD],
        "title": role.name if role else _("Add role"),
    })


@shop_permission_required("role.create")
def role_create(request):
    return _role_form(request)


@shop_permission_required("role.edit")
def role_edit(request, pk):
    return _role_form(request, get_object_or_404(Role.objects.all(), pk=pk))


@shop_permission_required("role.delete")
@require_POST
def role_delete(request, pk):
    role = get_object_or_404(Role.objects.all(), pk=pk)
    try:
        role.delete()
    except ProtectedError:
        messages.error(request, _("%(role)s is given to employees. Move them to another role first.") % {"role": role.name})
    else:
        messages.success(request, _("Role deleted."))
    return redirect("people:roles")
