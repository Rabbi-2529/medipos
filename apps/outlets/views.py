from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.billing.limits import PlanLimitReached, check_limit
from apps.core.shop_views import ShopCreateView, ShopListView, ShopToggleView, ShopUpdateView

from .forms import CounterForm, OutletForm
from .middleware import SESSION_KEY
from .models import Counter, Outlet

OUTLETS = reverse_lazy("outlets:list")
COUNTERS = reverse_lazy("outlets:counters")


def suggest_outlet_code():
    numbers = [int(code) for code in Outlet.objects.values_list("code", flat=True) if code.isdigit()]
    return f"{max(numbers, default=0) + 1:04d}"


# ---- Outlets -------------------------------------------------------------------

class OutletListView(ShopListView):
    permission = "outlet.list"
    template_name = "outlets/outlet_list.html"
    title = _("Outlets")

    def get_queryset(self):
        return Outlet.objects.prefetch_related("counters")


class OutletCreateView(ShopCreateView):
    permission = "outlet.create"
    form_class = OutletForm
    title = _("Add outlet")
    success_url = OUTLETS

    def get_initial(self):
        return {"code": suggest_outlet_code()}

    def form_valid(self, form):
        try:
            check_limit(self.request.tenant, "outlets")
        except PlanLimitReached as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        return super().form_valid(form)


class OutletUpdateView(ShopUpdateView):
    permission = "outlet.edit"
    form_class = OutletForm
    model = Outlet
    title = _("Edit outlet")
    success_url = OUTLETS

    def get_queryset(self):
        return Outlet.objects.all()


class OutletToggleView(ShopToggleView):
    permission = "outlet.delete"
    model = Outlet
    success_url = OUTLETS

    # Inactive outlets still count toward the plan limit, so reactivating needs no limit check.
    def can_deactivate(self, outlet):
        if not Outlet.objects.filter(is_active=True).exclude(pk=outlet.pk).exists():
            return _("You can't deactivate your only active outlet.")
        return None


@login_required
@require_POST
def enter_outlet(request, pk):
    """Switch the outlet this user is working in."""
    outlet = next((o for o in request.allowed_outlets if o.pk == pk), None)
    if outlet is None:
        raise Http404
    request.session[SESSION_KEY] = outlet.pk
    messages.success(request, _("You're now working in %(outlet)s.") % {"outlet": outlet.name})
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = "core:dashboard"
    return redirect(target)


# ---- Counters ------------------------------------------------------------------

class CounterListView(ShopListView):
    permission = "counter.list"
    template_name = "outlets/counter_list.html"
    title = _("Counters")

    def get_queryset(self):
        return Counter.objects.select_related("outlet")


class CounterCreateView(ShopCreateView):
    permission = "counter.create"
    form_class = CounterForm
    title = _("Add counter")
    success_url = COUNTERS

    def get_initial(self):
        return {"outlet": getattr(self.request.outlet, "pk", None)}


class CounterUpdateView(ShopUpdateView):
    permission = "counter.edit"
    form_class = CounterForm
    title = _("Edit counter")
    success_url = COUNTERS

    def get_queryset(self):
        return Counter.objects.all()


class CounterToggleView(ShopToggleView):
    permission = "counter.delete"
    model = Counter
    success_url = COUNTERS
