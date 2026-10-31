from django.apps import AppConfig


class CustomersConfig(AppConfig):
    """Customers, what they owe (ledger) and due collection."""

    name = "apps.customers"
    label = "customers"
