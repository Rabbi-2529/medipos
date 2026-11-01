from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.utils.html import escape, format_html, format_html_join
from django.utils.text import capfirst
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.datatables import datatable_json
from apps.people.access import shop_permission_required

from .models import AuditEntry
from .registry import AUDITED, KINDS

A = AuditEntry.Action
ACTION_LOOK = {A.CREATE: ("teal", "bi-plus-circle"), A.UPDATE: ("amber", "bi-pencil"), A.DELETE: ("red", "bi-trash3"),
               A.LOGIN: ("sky", "bi-box-arrow-in-right"), A.LOGOUT: ("slate", "bi-box-arrow-right"),
               A.LOGIN_FAILED: ("rose", "bi-shield-exclamation")}


def kind_label(label):
    """'sales.sale' -> 'Sale', in the page's language."""
    if label in KINDS:
        return str(KINDS[label])
    try:
        return capfirst(str(apps.get_model(label)._meta.verbose_name))
    except (LookupError, ValueError):
        return label


def field_label(label, name):
    try:
        return capfirst(str(apps.get_model(label)._meta.get_field(name).verbose_name))
    except (LookupError, ValueError, FieldDoesNotExist):  # a field removed since
        return name.replace("_", " ").capitalize()


def object_url(entry):
    rule = AUDITED.get(entry.model)
    if not rule or not rule.link or entry.action == A.DELETE or not entry.object_id.isdigit():
        return ""
    try:
        return reverse(rule.link, args=[int(entry.object_id)])
    except NoReverseMatch:
        return ""


def _value(value):
    if value is True:
        return _("Yes")
    if value is False:
        return _("No")
    return value if value not in (None, "") else "—"


def changes_list(entry):
    """[(field label, before, after)] for a change; [(field label, value, None)] for an added or deleted record."""
    rows = []
    for name, value in (entry.changes or {}).items():
        label = field_label(entry.model, name) if entry.model else name.replace("_", " ").capitalize()
        if entry.action == A.UPDATE and isinstance(value, list) and len(value) == 2:
            rows.append((label, _value(value[0]), _value(value[1])))
        else:
            rows.append((label, _value(value), None))
    return rows


def _look(entry):
    tone, icon = ACTION_LOOK[entry.action]
    return format_html('<span class="att-chip tone-{}"><i class="bi {}"></i> {}</span>', tone, icon, entry.get_action_display())


def _summary(entry):
    rows = changes_list(entry)[:3]
    if entry.action == A.UPDATE:
        items = format_html_join("", '<span class="audit-change"><b>{}</b> {} → {}</span>', ((l, b, a) for l, b, a in rows))
    elif entry.action in (A.CREATE, A.DELETE):
        items = format_html_join("", '<span class="audit-change"><b>{}</b> {}</span>', ((l, v) for l, v, _n in rows))
    else:
        return ""
    more = len(entry.changes or {}) - len(rows)
    return format_html("{}{}", items, format_html('<span class="text-body-secondary"> +{}</span>', more) if more > 0 else "")


def _row(entry):
    url = object_url(entry)
    what = escape(entry.object_repr or "—")
    if url:
        what = format_html('<a href="{}">{}</a>', url, entry.object_repr)
    return [
        format_html('<a href="{}">{}</a>', reverse("audit:entry", args=[entry.pk]), date_format(timezone.localtime(entry.created_at), "d M Y, h:i A")),
        format_html("{}<small class=\"d-block text-body-secondary\">{}</small>", entry.user_name or _("System"), entry.ip or ""),
        _look(entry),
        format_html("<small class=\"d-block text-body-secondary\">{}</small>{}", kind_label(entry.model) if entry.model else "", what),
        format_html('<div class="audit-summary">{}</div>', _summary(entry)),
    ]


def _filtered(request):
    queryset = AuditEntry.objects.select_related("user")
    get = request.GET
    if get.get("action") in A.values:
        queryset = queryset.filter(action=get["action"])
    if get.get("user", "").isdigit():
        queryset = queryset.filter(user_id=get["user"])
    if get.get("model"):
        queryset = queryset.filter(model=get["model"])
    if get.get("object"):
        queryset = queryset.filter(object_id=get["object"])
    start, end = parse_date(get.get("start") or ""), parse_date(get.get("end") or "")
    if start:
        queryset = queryset.filter(created_at__date__gte=start)
    if end:
        queryset = queryset.filter(created_at__date__lte=end)
    return queryset


@shop_permission_required("audit.list")
def audit_log(request):
    used = AuditEntry.objects.exclude(model="").values_list("model", flat=True).distinct().order_by()
    record = None
    if request.GET.get("model") and request.GET.get("object"):
        last = AuditEntry.objects.filter(model=request.GET["model"], object_id=request.GET["object"]).first()
        record = {"kind": kind_label(request.GET["model"]), "name": last.object_repr if last else request.GET["object"]}
    return render(request, "audit/log.html", {
        "actions": A.choices, "kinds": sorted(((m, kind_label(m)) for m in set(used)), key=lambda pair: pair[1]),
        "people": User.objects.filter(tenant=request.tenant).order_by("first_name", "email"), "record": record,
    })


@shop_permission_required("audit.list")
def audit_data(request):
    def search(queryset, term):
        return queryset.filter(Q(object_repr__icontains=term) | Q(user_name__icontains=term) | Q(ip__icontains=term) | Q(object_id=term))

    return datatable_json(request, _filtered(request).order_by("-created_at", "-id"),
                          order_columns=["created_at", "user_name", "action", "object_repr", None], row=_row, search=search)


@shop_permission_required("audit.list")
def audit_entry(request, pk):
    entry = get_object_or_404(AuditEntry.objects.select_related("user"), pk=pk)
    history = None
    if entry.model and entry.object_id:
        history = f"{reverse('audit:log')}?model={entry.model}&object={entry.object_id}"
    return render(request, "audit/entry.html", {
        "entry": entry, "look": _look(entry), "kind": kind_label(entry.model) if entry.model else "", "url": object_url(entry),
        "rows": changes_list(entry), "history": history,
    })
