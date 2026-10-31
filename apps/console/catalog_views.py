"""Platform console: the shared medicine catalog."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.catalog.models import CatalogMedicine
from apps.catalog.services import EXAMPLE_ROWS, HEADERS, import_catalog, search_catalog
from apps.core.datatables import datatable_json
from apps.core.forms import BootstrapFormMixin
from apps.core.spreadsheets import SpreadsheetError, xlsx_response

from .views import platform_admin_required


class CatalogMedicineForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CatalogMedicine
        fields = ["brand_name", "strength", "dosage_form", "generic_name", "manufacturer", "pack_size", "unit_price", "is_active"]


class CatalogImportForm(forms.Form):
    file = forms.FileField(label=gettext_lazy("Excel or CSV file"),
                           widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv", "class": "form-control"}))


@platform_admin_required
def catalog_list(request):
    return render(request, "console/catalog_list.html", {
        "total": CatalogMedicine.objects.count(),
        "import_form": CatalogImportForm(),
    })


@platform_admin_required
def catalog_data(request):
    def row(c):
        url = reverse("platform:catalog_edit", args=[c.pk])
        return [
            format_html('<a class="fw-semibold" href="{}">{}</a>', url, c.display_name),
            c.generic_name or "—",
            c.manufacturer or "—",
            c.pack_size or "—",
            f"৳{c.unit_price}" if c.unit_price is not None else "—",
            format_html('<span class="state-badge state-{}">{}</span>', "active" if c.is_active else "suspended",
                        _("Active") if c.is_active else _("Hidden")),
        ]

    return datatable_json(
        request, CatalogMedicine.objects.order_by("brand_name", "strength"),
        order_columns=["brand_name", "generic_name", "manufacturer", None, "unit_price", "is_active"],
        row=row, search=search_catalog,
    )


@platform_admin_required
def catalog_import(request):
    form = CatalogImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            created, updated, errors = import_catalog(form.cleaned_data["file"])
        except SpreadsheetError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Catalog import: %(created)s added, %(updated)s updated.") % {"created": created, "updated": updated})
            if errors:
                shown = "; ".join(f"row {n}: {msg}" for n, msg in errors[:10])
                messages.warning(request, _("%(count)s rows skipped. %(shown)s") % {"count": len(errors), "shown": shown})
    elif request.method == "POST":
        messages.error(request, _("Choose a file to import."))
    return redirect("platform:catalog")


@platform_admin_required
def catalog_template(request):
    return xlsx_response("catalog-template.xlsx", HEADERS, EXAMPLE_ROWS)


@platform_admin_required
def catalog_form(request, pk=None):
    entry = get_object_or_404(CatalogMedicine, pk=pk) if pk else None
    form = CatalogMedicineForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Catalog entry saved."))
        return redirect("platform:catalog")
    return render(request, "shop/form.html", {
        "form": form,
        "title": entry.display_name if entry else _("Add catalog medicine"),
        "cancel_url": reverse("platform:catalog"),
    })
