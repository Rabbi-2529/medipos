from django.contrib import admin

from .models import Plan, SubscriptionPayment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "price_monthly", "price_yearly", "max_outlets", "max_users", "max_medicines", "is_public", "is_active"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ["tenant", "plan", "amount", "months", "method", "period_start", "period_end", "paid_at"]
    list_filter = ["method", "plan"]
    search_fields = ["tenant__name", "reference"]
    raw_id_fields = ["tenant"]
