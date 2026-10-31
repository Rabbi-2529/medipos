from django.apps import AppConfig


class StockConfig(AppConfig):
    """Batches, per-outlet stock and the immutable stock movement ledger."""

    name = "apps.stock"
    label = "stock"
