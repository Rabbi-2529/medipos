"""Bulk import: upload -> checked preview -> confirm."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.billing.limits import PlanLimitReached, usage_summary
from apps.core import staging
from apps.core.spreadsheets import SpreadsheetError, xlsx_response
from apps.people.access import shop_permission_required

from .forms import ImportForm
from .importer import HEADERS, TEMPLATE_EXAMPLES, TEMPLATE_NOTES, parse_upload, rows_from_json, rows_to_json, run_import

SESSION_KEY = "item_import"


@shop_permission_required("item.create")
def import_template(request):
    return xlsx_response("medicine-import-template.xlsx", HEADERS, TEMPLATE_EXAMPLES, notes=TEMPLATE_NOTES)


@shop_permission_required("item.create")
def import_upload(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        update = form.cleaned_data["update_existing"]
        try:
            preview = parse_upload(form.cleaned_data["file"], update_existing=update)
        except (SpreadsheetError, ValueError) as error:
            form.add_error("file", str(error))
        else:
            if preview.missing_name_column:
                form.add_error("file", _("No “Name” column found. Use the template, or name your columns like it."))
            else:
                staging.stage(request, SESSION_KEY, {
                    "file": form.cleaned_data["file"].name,
                    "total": preview.total,
                    "columns": preview.columns,
                    "create": rows_to_json(preview.to_create),
                    "update": rows_to_json(preview.to_update),
                    "errors": preview.errors,
                })
                return redirect("items:import_preview")
    usage = next(row for row in usage_summary(request.tenant) if row["key"] == "medicines")
    return render(request, "items/import_upload.html", {"form": form, "usage": usage})


@shop_permission_required("item.create")
def import_preview(request):
    staged = staging.load(request, SESSION_KEY)
    if staged is None:
        messages.info(request, _("Upload a file to start an import."))
        return redirect("items:import")

    if request.method == "POST":
        if "cancel" in request.POST:
            staging.clear(request, SESSION_KEY)
            return redirect("items:import")
        try:
            created, updated = run_import(request.tenant, rows_from_json(staged["create"]), rows_from_json(staged["update"]))
        except PlanLimitReached as error:
            messages.error(request, str(error))
            return redirect("items:import_preview")
        staging.clear(request, SESSION_KEY)
        messages.success(request, _("Import finished: %(created)s added, %(updated)s updated.") % {"created": created, "updated": updated})
        return redirect("items:list")

    return render(request, "items/import_preview.html", {
        "staged": staged,
        "sample": staged["create"][:15],
        "update_sample": staged["update"][:10],
        "errors": staged["errors"][:200],
        "more_errors": max(0, len(staged["errors"]) - 200),
    })
