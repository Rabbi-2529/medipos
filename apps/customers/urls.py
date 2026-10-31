from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list, name="list"),
    path("data/", views.customer_data, name="data"),
    path("lookup/", views.customer_lookup, name="lookup"),
    path("quick-add/", views.customer_quick_create, name="quick_create"),
    path("new/", views.CustomerCreateView.as_view(), name="create"),
    path("<int:pk>/", views.customer_detail, name="detail"),
    path("<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="edit"),
    path("<int:pk>/toggle/", views.customer_toggle, name="toggle"),
    path("collections/", views.receipt_list, name="receipts"),
    path("collections/data/", views.receipt_data, name="receipt_data"),
    path("collections/new/", views.receipt_create, name="receipt_create"),
    path("collections/due-sales/", views.due_sales, name="due_sales"),
]
