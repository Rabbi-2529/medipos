from django.urls import path

from . import views

app_name = "setup"

urlpatterns = [
    path("", views.business_settings, name="settings"),
    path("invoice/", views.invoice_settings, name="invoice"),
    path("invoice/preview/", views.invoice_preview, name="invoice_preview"),
]
