from django.contrib import admin

from .models import CatalogMedicine


@admin.register(CatalogMedicine)
class CatalogMedicineAdmin(admin.ModelAdmin):
    list_display = ["brand_name", "strength", "dosage_form", "generic_name", "manufacturer", "unit_price", "is_active"]
    list_filter = ["dosage_form", "is_active"]
    search_fields = ["brand_name", "generic_name", "manufacturer"]
