from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "phone", "tenant", "is_platform_admin", "is_active"]
    list_filter = ["is_platform_admin", "is_active", "tenant"]
    search_fields = ["email", "phone", "first_name", "last_name"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Profile", {"fields": ["first_name", "last_name", "phone"]}),
        ("Tenant", {"fields": ["tenant", "is_platform_admin"]}),
        ("Permissions", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Dates", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "phone", "tenant", "password1", "password2"]}),
    ]
