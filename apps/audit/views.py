from django.apps import apps
from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.formats import date_format
from django.utils.html import escape, format_html, format_html_join
from django.utils.text import capfirst
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.datatables import datatable_json
from apps.people.access import has_perm, shop_permission_required

from . import branch
from .forms import FindingForm, SignOffForm, StartAuditForm
from .models import AuditCheck, AuditEntry, AuditFinding, BranchAudit
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


# ---- Branch audits ---------------------------------------------------------------------------

FLAG_LOOK = {AuditCheck.Flag.OK: ("teal", "bi-check-circle"), AuditCheck.Flag.WARN: ("amber", "bi-exclamation-triangle"),
             AuditCheck.Flag.INFO: ("sky", "bi-info-circle")}
VERDICT_TONE = {BranchAudit.Verdict.GOOD: "teal", BranchAudit.Verdict.WATCH: "amber", BranchAudit.Verdict.BAD: "red"}
SEVERITY_TONE = {AuditFinding.Severity.HIGH: "red", AuditFinding.Severity.MEDIUM: "amber", AuditFinding.Severity.LOW: "sky"}


@shop_permission_required("branch_audit.list")
def branch_audits(request):
    can_start = has_perm(request.user, "branch_audit.create")
    form = StartAuditForm(request.POST or None) if can_start else None
    if request.method == "POST":
        if not can_start:
            raise Http404
        if form.is_valid():
            data = form.cleaned_data
            audit = branch.start_audit(tenant=request.tenant, outlet=data["outlet"], start=data["start"], end=data["end"],
                                       user=request.user, note=data["note"])
            messages.success(request, _("%(number)s started: the figures are worked out. Go through the checklist.") % {"number": audit.number})
            return redirect("audit:branch", pk=audit.pk)
    audits = list(BranchAudit.objects.select_related("outlet", "created_by", "signed_by").annotate(
        issues=Count("checks", filter=Q(checks__result=AuditCheck.Result.ISSUE)),
        open_findings=Count("findings", filter=Q(findings__resolved=False), distinct=True)))
    for audit in audits:
        audit.verdict_tone = VERDICT_TONE.get(audit.verdict, "slate")
    return render(request, "audit/branch_list.html", {"audits": audits, "form": form})


def _audit(pk):
    return get_object_or_404(BranchAudit.objects.select_related("outlet", "created_by", "signed_by"), pk=pk)


@shop_permission_required("branch_audit.list")
def branch_audit_detail(request, pk):
    audit = _audit(pk)
    can_work = has_perm(request.user, "branch_audit.create") and audit.is_open
    can_sign = has_perm(request.user, "branch_audit.edit")
    finding_form = FindingForm(prefix="finding")
    sign_form = SignOffForm(prefix="sign", instance=audit)
    if request.method == "POST":
        action = request.POST.get("action")
        allowed = can_sign if action in ("sign", "reopen") else can_work
        if not allowed:
            raise Http404
        try:
            if action == "refresh":
                branch.refresh(audit)
                messages.success(request, _("Figures worked out again."))
            elif action == "checks":
                branch.save_checks(audit, {c.key: (request.POST.get(f"result-{c.key}", ""), request.POST.get(f"note-{c.key}", ""))
                                           for c in audit.checks.all()})
                messages.success(request, _("Checklist saved."))
            elif action == "finding":
                finding_form = FindingForm(request.POST, prefix="finding")
                if finding_form.is_valid():
                    branch.add_finding(audit, user=request.user, **finding_form.cleaned_data)
                    messages.success(request, _("Finding added."))
                    return redirect("audit:branch", pk=pk)
                raise branch.AuditError(_("Give the finding a title."))
            elif action in ("resolve", "delete_finding"):
                finding = get_object_or_404(AuditFinding, pk=request.POST.get("finding"), audit=audit)
                if action == "resolve":
                    finding.resolved = not finding.resolved
                    finding.save(update_fields=["resolved", "updated_at"])
                else:
                    finding.delete()
            elif action == "sign":
                sign_form = SignOffForm(request.POST, prefix="sign", instance=audit)
                if not sign_form.is_valid():
                    raise branch.AuditError(_("Choose the verdict."))
                branch.sign_off(audit, verdict=sign_form.cleaned_data["verdict"], summary=sign_form.cleaned_data["summary"], user=request.user)
                messages.success(request, _("%(number)s signed off.") % {"number": audit.number})
            elif action == "reopen":
                branch.reopen(audit)
                messages.success(request, _("%(number)s is open again.") % {"number": audit.number})
        except branch.AuditError as error:
            messages.error(request, str(error))
        return redirect("audit:branch", pk=pk)
    return render(request, "audit/branch_detail.html", _branch_context(audit) | {
        "can_work": can_work, "can_sign": can_sign, "finding_form": finding_form, "sign_form": sign_form})


def _branch_context(audit):
    checks = list(audit.checks.all())
    for check in checks:
        check.title = branch.CHECK_TITLES.get(check.key, check.key)
        check.tone, check.icon = FLAG_LOOK[check.flag]
    findings = branch.ordered_findings(audit)
    for finding in findings:
        finding.tone = SEVERITY_TONE[finding.severity]
    return {
        "audit": audit, "f": audit.figures, "checks": checks, "findings": findings, "results": AuditCheck.Result.choices,
        "verdict_tone": VERDICT_TONE.get(audit.verdict, "slate"),
        "warnings": sum(1 for c in checks if c.flag == AuditCheck.Flag.WARN),
        "issues": sum(1 for c in checks if c.result == AuditCheck.Result.ISSUE),
    }


@shop_permission_required("branch_audit.list")
def branch_audit_print(request, pk):
    from apps.setup.services import get_business_settings

    audit = _audit(pk)
    return render(request, "audit/branch_print.html", _branch_context(audit) | {
        "shop": get_business_settings(request.tenant), "tenant": request.tenant, "auto_print": request.GET.get("print") == "1"})
