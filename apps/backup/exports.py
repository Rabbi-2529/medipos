"""What a pharmacy can take away: an Excel workbook of the lists it picks, or a full backup (.zip of JSON plus uploaded files)."""
import io
import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.core import serializers
from django.core.files.storage import default_storage
from django.db import models
from django.db.models import IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from apps.core.models import TenantOwnedModel

SKIP = {"id", "tenant", "updated_at"}
# Never leave the server in an export: gateway keys (the shop can paste them again) and password hashes.
SECRET_FIELDS = {"messaging.messagesettings": {"sms_api_key", "whatsapp_api_key"}}
USER_HIDDEN = {"password", "is_superuser", "is_staff", "groups", "user_permissions"}
SPOOL = 32 * 1024 * 1024  # bigger files spill from memory to a temporary file


def _on_hand(queryset):
    BatchStock = apps.get_model("stock", "BatchStock")
    total = BatchStock.objects.filter(medicine=OuterRef("pk")).order_by().values("medicine").annotate(q=Sum("quantity")).values("q")
    return queryset.annotate(on_hand=Coalesce(Subquery(total, output_field=IntegerField()), Value(0))), [("on_hand", _("In stock (units)"))]


def _customer_balance(queryset):
    from apps.customers.services import with_balances

    return with_balances(queryset), [("balance", _("Owes now"))]


def _supplier_balance(queryset):
    from apps.suppliers.services import with_balances

    return with_balances(queryset), [("balance", _("We owe now"))]


@dataclass
class Dataset:
    key: str
    label: str
    group: str
    model: str                      # "app_label.ModelName"
    dated: str = ""                 # lookup filtered by the chosen period; "" = everything, whenever it was made
    extra: object = None            # (queryset) -> (queryset, [(attribute, label)]) for worked-out columns
    filters: dict = field(default_factory=dict)


GROUPS = [
    ("stock", _("Medicines & stock")), ("selling", _("Sales & customers")), ("buying", _("Purchases & suppliers")),
    ("money", _("Money & accounts")), ("people", _("Staff")), ("messages", _("Messages & loyalty")),
]

DATASETS = [
    Dataset("medicines", _("Medicines"), "stock", "items.Medicine", extra=_on_hand),
    Dataset("batches", _("Stock by batch"), "stock", "stock.BatchStock", filters={"quantity__gt": 0}),
    Dataset("stock_movements", _("Stock movements"), "stock", "stock.StockMovement", dated="created_at__date"),
    Dataset("customers", _("Customers"), "selling", "customers.Customer", extra=_customer_balance),
    Dataset("sales", _("Sales"), "selling", "sales.Sale", dated="sold_at__date"),
    Dataset("sale_items", _("Sale items"), "selling", "sales.SaleLine", dated="sale__sold_at__date"),
    Dataset("sale_returns", _("Sale returns"), "selling", "sales.SaleReturn", dated="date"),
    Dataset("collections", _("Due collections"), "selling", "customers.CustomerReceipt", dated="date"),
    Dataset("suppliers", _("Suppliers"), "buying", "suppliers.Supplier", extra=_supplier_balance),
    Dataset("purchases", _("Purchases"), "buying", "purchases.Purchase", dated="date"),
    Dataset("purchase_items", _("Purchase items"), "buying", "purchases.PurchaseLine", dated="purchase__date"),
    Dataset("purchase_returns", _("Purchase returns"), "buying", "purchases.PurchaseReturn", dated="date"),
    Dataset("supplier_payments", _("Supplier payments"), "buying", "suppliers.SupplierPayment", dated="date"),
    Dataset("cash_entries", _("Expenses & other income"), "money", "accounting.CashEntry", dated="date"),
    Dataset("transfers", _("Deposits & transfers"), "money", "accounting.FundMovement", dated="date"),
    Dataset("journal", _("Journal (double entry)"), "money", "accounting.JournalLine", dated="entry__date"),
    Dataset("employees", _("Employees"), "people", "people.Employee"),
    Dataset("attendance", _("Attendance"), "people", "hr.Attendance", dated="date"),
    Dataset("payroll", _("Payroll"), "people", "hr.PayrollLine", dated="payroll__month"),
    Dataset("audit", _("Audit log"), "people", "audit.AuditEntry", dated="created_at__date"),
    Dataset("messages", _("Messages sent"), "messages", "messaging.Message", dated="created_at__date"),
    Dataset("points", _("Loyalty points"), "messages", "messaging.PointEntry", dated="date"),
]
BY_KEY = {d.key: d for d in DATASETS}


def _cell(value):
    """A value Excel can hold: local times without a zone, records as their name, lists as JSON."""
    if value is None or isinstance(value, (bool, int, float, Decimal)):
        return value
    if isinstance(value, datetime):
        return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
    if isinstance(value, date):
        return value
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))


def dataset_rows(dataset, start=None, end=None):
    """(headers, rows) for one list: every stored column (links shown by name) plus any worked-out ones."""
    model = apps.get_model(dataset.model)
    hidden = SECRET_FIELDS.get(model._meta.label_lower, set())
    fields = sorted((f for f in model._meta.concrete_fields if f.name not in SKIP and f.name not in hidden),
                    key=lambda f: f.name == "created_at")  # the record's own columns first, when it was made last
    queryset = model.objects.filter(**dataset.filters).select_related(*[f.name for f in fields if f.is_relation])
    if dataset.dated and start and end:
        queryset = queryset.filter(**{f"{dataset.dated}__gte": start, f"{dataset.dated}__lte": end})
    extras = []
    if dataset.extra:
        queryset, extras = dataset.extra(queryset)
    headers = [capfirst(str(f.verbose_name)) for f in fields] + [str(label) for _attr, label in extras]

    def rows():
        for obj in queryset.order_by("pk").iterator(chunk_size=2000):
            row = []
            for f in fields:
                if f.is_relation:
                    value = getattr(obj, f.name)
                elif f.choices:
                    value = getattr(obj, f"get_{f.name}_display")()
                else:
                    value = getattr(obj, f.attname)
                row.append(_cell(value))
            yield row + [_cell(getattr(obj, attr)) for attr, _label in extras]

    return headers, rows()


def _sheet_title(label, used):
    title = "".join(c for c in str(label) if c not in "[]:*?/\\")[:31]
    while title in used:
        title = title[:28] + f" {len(used)}"
    used.add(title)
    return title


def excel_workbook(keys, start=None, end=None):
    """The chosen lists, one sheet each, in a temporary .xlsx. Returns (file, rows written)."""
    book = Workbook(write_only=True)
    bold = Font(bold=True)
    used, total = set(), 0
    for key in keys:
        dataset = BY_KEY[key]
        headers, rows = dataset_rows(dataset, start, end)
        sheet = book.create_sheet(title=_sheet_title(dataset.label, used))
        header_cells = []
        for text in headers:
            cell = WriteOnlyCell(sheet, value=text)
            cell.font = bold
            header_cells.append(cell)
        sheet.append(header_cells)
        for row in rows:
            sheet.append(row)
            total += 1
    out = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    book.save(out)
    out.seek(0)
    return out, total


def tenant_models():
    """Every kind of record a pharmacy owns."""
    return sorted((m for m in apps.get_models() if issubclass(m, TenantOwnedModel)), key=lambda m: m._meta.label_lower)


def _write_json(archive, name, objects, fields=None):
    with archive.open(name, "w") as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
        serializers.serialize("json", objects, stream=text, fields=fields, indent=1, ensure_ascii=False)


README = """ThingBD MediPOS backup
======================

data/        one JSON file per kind of record (Django's serialisation format: model, pk, fields).
media/       uploaded files: logo, letterhead, medicine pictures.
manifest.json  which pharmacy, when, by whom, and how many records of each kind.

Left out on purpose: password hashes and SMS/WhatsApp gateway API keys.
Keep this file somewhere safe: it holds your customers' names and phone numbers.
To restore it into MediPOS, contact ThingBD support.
"""


def full_backup(tenant, *, user=None):
    """Everything the pharmacy owns, as a .zip in a temporary file. Returns (file, records). Run inside the tenant's context."""
    from apps.accounts.models import User

    out = tempfile.SpooledTemporaryFile(max_size=SPOOL)
    counts, files = {}, set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        _write_json(archive, "data/tenants.tenant.json", [tenant])
        users = User.objects.filter(tenant=tenant).order_by("pk")
        user_fields = [f.name for f in User._meta.get_fields()
                       if (getattr(f, "concrete", False) or f.many_to_many) and not f.auto_created and f.name not in USER_HIDDEN]
        _write_json(archive, "data/accounts.user.json", users, fields=user_fields)
        counts["accounts.user"] = users.count()
        for model in tenant_models():
            label = model._meta.label_lower
            hidden = SECRET_FIELDS.get(label)
            fields = None
            if hidden:
                fields = [f.name for f in [*model._meta.concrete_fields, *model._meta.many_to_many] if f.name not in hidden and not f.primary_key]
            queryset = model.objects.order_by("pk")
            counts[label] = queryset.count()
            if not counts[label]:
                continue
            _write_json(archive, f"data/{label}.json", queryset.iterator(chunk_size=2000), fields=fields)
            for f in model._meta.concrete_fields:
                if isinstance(f, models.FileField):
                    files.update(name for name in queryset.exclude(**{f.name: ""}).values_list(f.name, flat=True) if name)
        for name in sorted(files):
            if default_storage.exists(name):
                with default_storage.open(name, "rb") as source:
                    archive.writestr(f"media/{name}", source.read())
        manifest = {
            "app": "ThingBD MediPOS", "format": 1, "created_at": timezone.now().isoformat(),
            "pharmacy": {"id": tenant.pk, "name": tenant.name, "slug": tenant.slug},
            "created_by": getattr(user, "email", None), "records": counts, "files": len(files),
            "left_out": ["password hashes", "SMS and WhatsApp gateway API keys"],
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr("README.txt", README)
    out.seek(0)
    return out, sum(counts.values())


def size_of(handle):
    position = handle.tell()
    handle.seek(0, 2)
    size = handle.tell()
    handle.seek(position)
    return size
