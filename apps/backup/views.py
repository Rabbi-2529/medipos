from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.spreadsheets import XLSX
from apps.people.access import shop_permission_required

from .exports import DATASETS, GROUPS, excel_workbook, full_backup, size_of
from .forms import ExcelExportForm
from .models import ExportLog


def backup_response(tenant, user):
    """The full backup as a download, written down in the export log. Run inside the tenant's context."""
    archive, records = full_backup(tenant, user=user)
    ExportLog.objects.create(kind=ExportLog.Kind.BACKUP, rows=records, size=size_of(archive), created_by=user)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    return FileResponse(archive, as_attachment=True, filename=f"medipos-backup-{tenant.slug}-{stamp}.zip", content_type="application/zip")


@shop_permission_required("backup.create")
def data_page(request):
    """Take the pharmacy's data away: an Excel workbook of chosen lists, or a full backup."""
    form = ExcelExportForm(request.POST if request.POST.get("action") == "excel" else None)
    if request.method == "POST":
        if request.POST.get("action") == "backup":
            return backup_response(request.tenant, request.user)
        if form.is_valid():
            keys = [d.key for d in DATASETS if d.key in form.cleaned_data["datasets"]]
            start, end = form.cleaned_data["start"], form.cleaned_data["end"]
            workbook, rows = excel_workbook(keys, start, end)
            ExportLog.objects.create(kind=ExportLog.Kind.EXCEL, datasets=keys, start=start, end=end, rows=rows,
                                     size=size_of(workbook), created_by=request.user)
            return FileResponse(workbook, as_attachment=True, content_type=XLSX,
                                filename=f"medipos-{request.tenant.slug}-{start:%Y%m%d}-{end:%Y%m%d}.xlsx")
        messages.error(request, _("Check the lists and dates."))
    return render(request, "backup/data.html", {
        "form": form, "groups": [(label, [d for d in DATASETS if d.group == key]) for key, label in GROUPS],
        "checked": set(form["datasets"].value() or []),
        "dated": {d.key for d in DATASETS if d.dated},
        "log": ExportLog.objects.select_related("created_by")[:15],
    })
