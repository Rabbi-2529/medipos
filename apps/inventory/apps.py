from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Stock transfers between outlets, damage/expired write-offs and stock counts."""

    name = "apps.inventory"
    label = "inventory"
