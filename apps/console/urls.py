from django.urls import path

from . import views

app_name = "platform"

urlpatterns = [
    path("pharmacies/", views.tenant_list, name="tenants"),
    path("pharmacies/new/", views.tenant_create, name="tenant_create"),
    path("pharmacies/data/", views.tenant_data, name="tenant_data"),
    path("pharmacies/<int:pk>/", views.tenant_detail, name="tenant_detail"),
    path("pharmacies/<int:pk>/<slug:action>/", views.tenant_action, name="tenant_action"),
    path("plans/", views.plan_list, name="plans"),
    path("plans/new/", views.plan_form, name="plan_create"),
    path("plans/<int:pk>/edit/", views.plan_form, name="plan_edit"),
]
