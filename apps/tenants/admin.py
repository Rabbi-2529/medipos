from django.contrib import admin

from .models import Tenant, TenantEvent


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "plan", "status", "trial_ends_at", "paid_until", "phone", "created_at"]
    list_filter = ["status", "plan"]
    search_fields = ["name", "slug", "phone", "email"]
    prepopulated_fields = {"slug": ["name"]}

    def has_add_permission(self, request):
        # Pharmacies need an owner and a first outlet: add them from /platform/pharmacies/new/.
        return False


@admin.register(TenantEvent)
class TenantEventAdmin(admin.ModelAdmin):
    list_display = ["tenant", "kind", "message", "actor", "created_at"]
    list_filter = ["kind"]
    search_fields = ["tenant__name", "message"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
