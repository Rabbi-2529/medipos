from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("payment-accounts/", views.PaymentAccountListView.as_view(), name="payment_accounts"),
    path("payment-accounts/new/", views.PaymentAccountCreateView.as_view(), name="payment_account_create"),
    path("payment-accounts/<int:pk>/edit/", views.PaymentAccountUpdateView.as_view(), name="payment_account_edit"),
    path("payment-accounts/<int:pk>/toggle/", views.PaymentAccountToggleView.as_view(), name="payment_account_toggle"),
]
