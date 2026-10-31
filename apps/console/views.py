from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.html import escape, format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.billing import services as billing
from apps.billing.limits import usage_summary
from apps.billing.models import Plan
from apps.billing.templatetags.billing_tags import state_badge_html
from apps.core.datatables import datatable_json
from apps.outlets.models import Outlet
from apps.tenants.models import AccessState, Tenant
from apps.tenants.services import register_pharmacy

from .forms import AddPharmacyForm, ChangePlanForm, ExtendTrialForm, PaymentForm, PlanForm, SuspendForm


def platform_admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_platform_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


# ---- Pharmacies ---------------------------------------------------------------

@platform_admin_required
def tenant_list(request):
    return render(request, "console/tenant_list.html", {
        "states": AccessState.choices,
        "plans": Plan.objects.all(),
    })


@platform_admin_required
def tenant_create(request):
    form = AddPharmacyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        tenant, owner = register_pharmacy(
            business_name=data["business_name"],
            business_phone=data["business_phone"],
            address=data["address"],
            plan=data["plan"],
            owner_name=data["owner_name"],
            email=data["email"],
            phone=data["phone"],
            password=data["password1"],
            trial_days=data["trial_days"],
            created_by=request.user,
        )
        messages.success(request, _("%(name)s is ready. The owner can log in with %(email)s.") % {
            "name": tenant.name, "email": owner.email,
        })
        return redirect("platform:tenant_detail", pk=tenant.pk)
    return render(request, "console/tenant_form.html", {"form": form})


def _search_tenants(queryset, term):
    owner_tenants = User.objects.filter(
        Q(email__icontains=term) | Q(phone__icontains=term) | Q(first_name__icontains=term)
    ).values("tenant_id")
    return queryset.filter(
        Q(name__icontains=term) | Q(slug__icontains=term) | Q(phone__icontains=term)
        | Q(email__icontains=term) | Q(pk__in=owner_tenants)
    )


def _tenant_row(tenant):
    url = reverse("platform:tenant_detail", args=[tenant.pk])
    owner = tenant.owner_list[0] if tenant.owner_list else None
    ends = tenant.ends_on()
    return [
        format_html('<a class="fw-semibold" href="{}">{}</a><small class="d-block text-body-secondary">{}</small>',
                    url, tenant.name, tenant.phone or tenant.slug),
        format_html('{}<small class="d-block text-body-secondary">{}</small>',
                    owner.get_full_name() or owner.email, owner.email) if owner else "—",
        escape(tenant.plan.name) if tenant.plan else "—",
        state_badge_html(tenant.access_state()),
        date_format(ends, "d M Y") if ends else "—",
        tenant.user_count,
        date_format(timezone.localtime(tenant.created_at), "d M Y"),
        format_html('<a class="btn btn-sm btn-outline-primary" href="{}">{}</a>', url, _("Manage")),
    ]


@platform_admin_required
def tenant_data(request):
    queryset = (
        Tenant.objects.select_related("plan")
        .prefetch_related(Prefetch("users", queryset=User.objects.filter(is_owner=True), to_attr="owner_list"))
        .annotate(user_count=Count("users", filter=Q(users__is_active=True), distinct=True))
    )
    state = request.GET.get("state")
    if state in AccessState.values:
        queryset = queryset.in_state(state)
    plan = request.GET.get("plan", "")
    if plan.isdigit():
        queryset = queryset.filter(plan_id=plan)
    return datatable_json(
        request, queryset.order_by("-created_at"),
        order_columns=["name", None, "plan__name", "status", None, "user_count", "created_at", None],
        row=_tenant_row,
        search=_search_tenants,
    )


@platform_admin_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    plans = Plan.objects.filter(is_active=True)
    days_left = tenant.days_left()
    return render(request, "console/tenant_detail.html", {
        "tenant": tenant,
        "state": tenant.access_state(),
        "days_left": days_left,
        "days_overdue": -days_left if days_left is not None and days_left < 0 else 0,
        "usage": usage_summary(tenant),
        "payments": tenant.payments.select_related("plan", "recorded_by")[:50],
        "events": tenant.events.select_related("actor")[:50],
        "users": tenant.users.order_by("-is_owner", "date_joined"),
        "outlets": Outlet.all_objects.filter(tenant=tenant),
        # Distinct auto_ids: both forms have a "plan" field and Select2 keys its widget off the HTML id.
        "payment_form": PaymentForm(initial={"plan": tenant.plan_id}, auto_id="pay_%s"),
        "plan_form": ChangePlanForm(initial={"plan": tenant.plan_id}, auto_id="chg_%s"),
        "trial_form": ExtendTrialForm(auto_id="trial_%s"),
        "suspend_form": SuspendForm(auto_id="susp_%s"),
        "plan_prices": {
            p.pk: {"monthly": str(p.price_monthly), "yearly": str(p.price_yearly) if p.price_yearly is not None else None}
            for p in plans
        },
    })


def _first_error(form):
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        return f"{label}: {errors[0]}" if label else errors[0]
    return _("Please check the form.")


@platform_admin_required
@require_POST
def tenant_action(request, pk, action):
    tenant = get_object_or_404(Tenant, pk=pk)
    actor = request.user
    forms = {"payment": PaymentForm, "plan": ChangePlanForm, "trial": ExtendTrialForm, "suspend": SuspendForm}
    if action not in forms and action != "reactivate":
        raise Http404
    form = forms[action](request.POST) if action in forms else None
    if form is not None and not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("platform:tenant_detail", pk=pk)

    try:
        if action == "payment":
            data = form.cleaned_data
            payment = billing.record_payment(
                tenant, plan=data["plan"], months=data["months"], amount=data["amount"], method=data["method"],
                reference=data["reference"], note=data["note"], actor=actor,
            )
            messages.success(request, _("Payment recorded. Paid until %(end)s.") % {"end": payment.period_end.strftime("%d %b %Y")})
        elif action == "plan":
            billing.change_plan(tenant, form.cleaned_data["plan"], actor=actor)
            messages.success(request, _("Plan changed."))
        elif action == "trial":
            billing.extend_trial(tenant, form.cleaned_data["days"], actor=actor)
            messages.success(request, _("Trial extended."))
        elif action == "suspend":
            billing.suspend(tenant, form.cleaned_data["reason"], actor=actor)
            messages.success(request, _("Pharmacy suspended."))
        else:
            billing.reactivate(tenant, actor=actor)
            messages.success(request, _("Pharmacy reactivated."))
    except billing.SubscriptionError as error:
        messages.error(request, str(error))
    return redirect("platform:tenant_detail", pk=pk)


# ---- Plans --------------------------------------------------------------------

@platform_admin_required
def plan_list(request):
    plans = Plan.objects.annotate(tenant_count=Count("tenants"))
    return render(request, "console/plan_list.html", {"plans": plans})


@platform_admin_required
def plan_form(request, pk=None):
    plan = get_object_or_404(Plan, pk=pk) if pk else None
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Plan saved."))
        return redirect("platform:plans")
    return render(request, "console/plan_form.html", {"form": form, "plan": plan})
