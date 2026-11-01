from django.urls import path

from apps.support import views as support_views

from . import catalog_views, views

app_name = "platform"

urlpatterns = [
    path("support/", views.platform_admin_required(support_views.platform_settings), name="support"),
    path("catalog/", catalog_views.catalog_list, name="catalog"),
    path("catalog/data/", catalog_views.catalog_data, name="catalog_data"),
    path("catalog/import/", catalog_views.catalog_import, name="catalog_import"),
    path("catalog/template.xlsx", catalog_views.catalog_template, name="catalog_template"),
    path("catalog/new/", catalog_views.catalog_form, name="catalog_create"),
    path("catalog/<int:pk>/edit/", catalog_views.catalog_form, name="catalog_edit"),
    path("pharmacies/", views.tenant_list, name="tenants"),
    path("pharmacies/new/", views.tenant_create, name="tenant_create"),
    path("pharmacies/data/", views.tenant_data, name="tenant_data"),
    path("pharmacies/<int:pk>/", views.tenant_detail, name="tenant_detail"),
    path("pharmacies/<int:pk>/backup/", views.tenant_backup, name="tenant_backup"),
    path("pharmacies/<int:pk>/<slug:action>/", views.tenant_action, name="tenant_action"),
    path("plans/", views.plan_list, name="plans"),
    path("plans/new/", views.plan_form, name="plan_create"),
    path("plans/<int:pk>/edit/", views.plan_form, name="plan_edit"),
]
