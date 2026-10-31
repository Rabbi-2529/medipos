"""Generic pharmacy screens: list / add / edit / activate-deactivate behind a permission code."""
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.people.access import ShopPermissionMixin


class ShopListView(ShopPermissionMixin, ListView):
    """Lists the current tenant's rows (Model.objects is already tenant-scoped)."""

    title = ""
    create_url = None
    create_permission = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(title=self.title, create_url=self.create_url, create_permission=self.create_permission)
        return context


class ShopFormMixin(ShopPermissionMixin, SuccessMessageMixin):
    template_name = "shop/form.html"
    title = ""
    success_url = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(title=self.title, cancel_url=self.success_url)
        return context


class ShopCreateView(ShopFormMixin, CreateView):
    success_message = _("Saved.")


class ShopUpdateView(ShopFormMixin, UpdateView):
    success_message = _("Changes saved.")


class ShopToggleView(ShopPermissionMixin, View):
    """POST: flip is_active. Override `can_deactivate` to block it."""

    model = None
    success_url = None

    def can_deactivate(self, obj):
        return None  # or a message explaining why not

    def post(self, request, pk):
        obj = get_object_or_404(self.model.objects, pk=pk)
        if obj.is_active:
            reason = self.can_deactivate(obj)
            if reason:
                messages.error(request, reason)
                return redirect(self.success_url)
        obj.is_active = not obj.is_active
        obj.save(update_fields=["is_active", "updated_at"])
        messages.success(request, (_("%(name)s activated.") if obj.is_active else _("%(name)s deactivated.")) % {"name": obj})
        return redirect(self.success_url)
