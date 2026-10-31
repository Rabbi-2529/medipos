"""Setup lists (categories, generics, manufacturers, units, racks) and Select2 lookups."""
from django.contrib import messages
from django.db.models import Count
from django.forms import modelform_factory
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.core.forms import TenantModelForm
from apps.people.access import has_perm, shop_permission_required

from .models import Category, Generic, Manufacturer, Medicine, Rack, Unit
from .services import search_medicines

MASTERS = {
    "categories": (Category, gettext_lazy("Categories"), gettext_lazy("Category"),
                   gettext_lazy("Groups for medicines and shop items, e.g. Vitamins, Baby care.")),
    "generics": (Generic, gettext_lazy("Generics"), gettext_lazy("Generic"),
                 gettext_lazy("Active ingredients. Staff can find every brand of a generic at the counter.")),
    "manufacturers": (Manufacturer, gettext_lazy("Manufacturers"), gettext_lazy("Manufacturer"),
                      gettext_lazy("Pharmaceutical companies and brand owners.")),
    "units": (Unit, gettext_lazy("Units"), gettext_lazy("Unit"),
              gettext_lazy("How items are counted and bought: Piece, Strip, Box, Bottle…")),
    "racks": (Rack, gettext_lazy("Racks"), gettext_lazy("Rack"),
              gettext_lazy("Shelf locations, so staff can find items quickly.")),
}
# Units are referenced twice on a medicine (sale and purchase unit) and have no single reverse relation to count.
COUNTED = {"categories", "generics", "manufacturers", "racks"}


def _master(kind):
    if kind not in MASTERS:
        raise Http404
    return MASTERS[kind]


def _form_class(model):
    return modelform_factory(model, form=TenantModelForm, fields=["name"])


@shop_permission_required("item_setup.list")
def master_list(request, kind):
    model, plural, singular, help_text = _master(kind)
    return render(request, "items/master_list.html", {
        "kind": kind, "title": plural, "singular": singular, "help_text": help_text,
        "counted": kind in COUNTED,
        "data_url": reverse(f"items:{kind}_data"),
        "create_url": reverse(f"items:{kind}_create"),
    })


@shop_permission_required("item_setup.list")
def master_data(request, kind):
    model = _master(kind)[0]
    queryset = model.objects.all()
    if kind in COUNTED:
        queryset = queryset.annotate(medicine_count=Count("medicines"))
    can_edit, can_toggle = has_perm(request.user, "item_setup.edit"), has_perm(request.user, "item_setup.delete")
    csrf_token = get_token(request)

    def row(obj):
        actions = []
        if can_edit:
            actions.append(format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>',
                                       reverse(f"items:{kind}_edit", args=[obj.pk]), _("Edit")))
        if can_toggle:
            actions.append(format_html(
                '<form method="post" action="{}" class="d-inline"><input type="hidden" name="csrfmiddlewaretoken" value="{}">'
                '<button class="btn btn-sm {}" type="submit">{}</button></form>',
                reverse(f"items:{kind}_toggle", args=[obj.pk]), csrf_token,
                "btn-outline-danger" if obj.is_active else "btn-outline-success",
                _("Deactivate") if obj.is_active else _("Activate"),
            ))
        status = format_html('<span class="state-badge state-{}">{}</span>', "active" if obj.is_active else "suspended",
                             _("Active") if obj.is_active else _("Inactive"))
        return [format_html('<span class="fw-semibold">{}</span>', obj.name),
                obj.medicine_count if kind in COUNTED else "—", status, " ".join(str(a) for a in actions)]

    return datatable_json(
        request, queryset.order_by("name"),
        order_columns=["name", "medicine_count" if kind in COUNTED else None, "is_active", None],
        row=row, search=lambda qs, term: qs.filter(name__icontains=term),
    )


def _master_form(request, kind, obj=None):
    model, plural, singular, _help = _master(kind)
    form = _form_class(model)(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("%(name)s saved.") % {"name": form.instance.name})
        return redirect(f"items:{kind}")
    title = (_("Edit %(thing)s") if obj else _("Add %(thing)s")) % {"thing": str(singular).lower()}
    return render(request, "shop/form.html", {"form": form, "title": title, "cancel_url": reverse(f"items:{kind}")})


@shop_permission_required("item_setup.create")
def master_create(request, kind):
    return _master_form(request, kind)


@shop_permission_required("item_setup.edit")
def master_edit(request, kind, pk):
    return _master_form(request, kind, get_object_or_404(_master(kind)[0].objects.all(), pk=pk))


@shop_permission_required("item_setup.delete")
@require_POST
def master_toggle(request, kind, pk):
    obj = get_object_or_404(_master(kind)[0].objects.all(), pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])
    messages.success(request, (_("%(name)s activated.") if obj.is_active else _("%(name)s deactivated.")) % {"name": obj.name})
    return redirect(f"items:{kind}")


# ---- Select2 lookups -------------------------------------------------------------

PAGE = 30


@shop_permission_required("item.list")
def lookup(request, kind):
    """Select2 AJAX source. Setup lists return names (so new ones can be typed); medicines return ids."""
    term = request.GET.get("q", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start = (page - 1) * PAGE
    if kind == "medicines":
        queryset = search_medicines(Medicine.objects.filter(is_active=True), term) if term else Medicine.objects.filter(is_active=True)
        rows = list(queryset.select_related("generic").order_by("name", "strength")[start:start + PAGE + 1])
        results = [{"id": m.pk, "text": m.display_name, "generic": m.generic.name if m.generic else "",
                    "code": m.code, "barcode": m.barcode, "price": str(m.sale_price)} for m in rows[:PAGE]]
    else:
        model = _master(kind)[0]
        queryset = model.objects.filter(is_active=True)
        if term:
            queryset = queryset.filter(name__icontains=term)
        rows = list(queryset.order_by("name").values_list("name", flat=True)[start:start + PAGE + 1])
        results = [{"id": name, "text": name} for name in rows[:PAGE]]
    return JsonResponse({"results": results, "pagination": {"more": len(rows) > PAGE}})
