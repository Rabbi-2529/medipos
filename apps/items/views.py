from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.billing.limits import PlanLimitReached, check_limit, usage_summary
from apps.core.datatables import datatable_json
from apps.core.spreadsheets import xlsx_response
from apps.people.access import has_perm, shop_permission_required
from apps.setup.services import format_money, get_business_settings

from .forms import MedicineForm
from .importer import HEADERS, export_rows
from .models import Medicine
from .services import search_medicines


def filtered_medicines(params):
    """Medicines matching the list page filters (shared by the table, export and bulk tools)."""
    queryset = Medicine.objects.all()
    if params.get("category"):
        queryset = queryset.filter(category__name=params["category"])
    if params.get("manufacturer"):
        queryset = queryset.filter(manufacturer__name=params["manufacturer"])
    if params.get("generic"):
        queryset = queryset.filter(generic__name=params["generic"])
    if params.get("kind") in Medicine.Kind.values:
        queryset = queryset.filter(kind=params["kind"])
    if params.get("rx") == "yes":
        queryset = queryset.filter(prescription_required=True)
    status = params.get("status", "active")
    if status in ("active", "inactive"):
        queryset = queryset.filter(is_active=status == "active")
    term = params.get("q", "").strip()
    if term:
        queryset = search_medicines(queryset, term)
    return queryset


@shop_permission_required("item.list")
def medicine_list(request):
    usage = next(row for row in usage_summary(request.tenant) if row["key"] == "medicines")
    return render(request, "items/medicine_list.html", {"usage": usage, "kinds": Medicine.Kind.choices})


@shop_permission_required("item.list")
def medicine_data(request):
    settings = get_business_settings(request.tenant)
    can_edit = has_perm(request.user, "item.edit")
    params = request.GET.copy()
    params.pop("q", None)  # DataTables sends its own search box value
    queryset = filtered_medicines(params).select_related("generic", "manufacturer", "category", "rack", "sale_unit", "purchase_unit")

    def row(m):
        url = reverse("items:edit", args=[m.pk])
        name = format_html('<a class="fw-semibold" href="{}">{}</a>', url, m.display_name) if can_edit else format_html('<span class="fw-semibold">{}</span>', m.display_name)
        badges = format_html(' <span class="badge text-bg-danger" title="{}">Rx</span>', _("Prescription required")) if m.prescription_required else ""
        pack = m.sale_unit.name if m.conversion == 1 else f"{m.purchase_unit.name} of {m.conversion}"
        status = format_html('<span class="state-badge state-{}">{}</span>', "active" if m.is_active else "suspended",
                             _("Active") if m.is_active else _("Inactive"))
        return [
            format_html("<code>{}</code>", m.code),
            format_html('{}{}<small class="d-block text-body-secondary">{}</small>', name, badges, m.generic.name if m.generic else ""),
            m.manufacturer.name if m.manufacturer else "—",
            m.category.name if m.category else "—",
            pack,
            format_money(m.purchase_price, settings),
            format_money(m.sale_price, settings),
            m.rack.name if m.rack else "—",
            status,
            format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>', url, _("Edit")) if can_edit else "",
        ]

    return datatable_json(
        request, queryset.order_by("name", "strength"),
        order_columns=["code", "name", "manufacturer__name", "category__name", None, "purchase_price", "sale_price", "rack__name", "is_active", None],
        row=row,
        search=search_medicines,
    )


@shop_permission_required("item.list")
def medicine_export(request):
    queryset = filtered_medicines(request.GET).order_by("name", "strength")
    return xlsx_response("medicines.xlsx", HEADERS, export_rows(queryset))


def _medicine_form(request, medicine=None):
    form = MedicineForm(request.POST or None, request.FILES or None, instance=medicine)
    if request.method == "POST" and form.is_valid():
        try:
            if medicine is None:
                check_limit(request.tenant, "medicines")
        except PlanLimitReached as error:
            form.add_error(None, str(error))
        else:
            saved = form.save()
            messages.success(request, _("%(name)s saved (code %(code)s).") % {"name": saved.display_name, "code": saved.code})
            if "save_add" in request.POST:
                return redirect("items:create")
            return redirect("items:list")
    return render(request, "items/medicine_form.html", {
        "form": form,
        "medicine": medicine,
        "title": medicine.display_name if medicine else _("Add medicine"),
        "currency": get_business_settings(request.tenant).currency_symbol,
    })


@shop_permission_required("item.create")
def medicine_create(request):
    return _medicine_form(request)


@shop_permission_required("item.edit")
def medicine_edit(request, pk):
    return _medicine_form(request, get_object_or_404(Medicine.objects.all(), pk=pk))


@shop_permission_required("item.delete")
@require_POST
def medicine_toggle(request, pk):
    medicine = get_object_or_404(Medicine.objects.all(), pk=pk)
    medicine.is_active = not medicine.is_active
    medicine.save(update_fields=["is_active", "updated_at"])
    messages.success(request, (_("%(name)s is active again.") if medicine.is_active else _("%(name)s is hidden from sales.")) % {"name": medicine.display_name})
    return redirect("items:edit", pk=pk)
