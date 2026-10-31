from django.contrib import admin

from .models import Outlet


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):
    """Read-only view across all pharmacies (outlets are managed in the app)."""

    list_display = ["name", "code", "tenant", "phone", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code", "tenant__name"]

    def get_queryset(self, request):
        return Outlet.all_objects.select_related("tenant")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
