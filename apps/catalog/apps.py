from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Platform-wide medicine catalog that pharmacies pick their medicines from."""

    name = "apps.catalog"
    label = "catalog"
