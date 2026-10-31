from django.urls import path

from . import views

app_name = "suppliers"

urlpatterns = [
    path("", views.supplier_list, name="list"),
    path("data/", views.supplier_data, name="data"),
    path("lookup/", views.supplier_lookup, name="lookup"),
    path("new/", views.SupplierCreateView.as_view(), name="create"),
    path("<int:pk>/", views.supplier_detail, name="detail"),
    path("<int:pk>/edit/", views.SupplierUpdateView.as_view(), name="edit"),
    path("<int:pk>/toggle/", views.supplier_toggle, name="toggle"),
    path("payments/", views.payment_list, name="payments"),
    path("payments/data/", views.payment_data, name="payment_data"),
    path("payments/new/", views.payment_create, name="payment_create"),
]
