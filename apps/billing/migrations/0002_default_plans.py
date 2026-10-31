from decimal import Decimal

from django.db import migrations

# Placeholder prices (BDT) — edit them from the platform console.
DEFAULT_PLANS = [
    {
        "slug": "starter", "name": "Starter", "sort_order": 1,
        "description": "One pharmacy counter getting started.",
        "price_monthly": Decimal("1000"), "price_yearly": Decimal("10000"),
        "max_outlets": 1, "max_users": 3, "max_medicines": 3000,
    },
    {
        "slug": "business", "name": "Business", "sort_order": 2,
        "description": "Growing pharmacies with a few branches.",
        "price_monthly": Decimal("2500"), "price_yearly": Decimal("25000"),
        "max_outlets": 3, "max_users": 10, "max_medicines": None,
    },
    {
        "slug": "enterprise", "name": "Enterprise", "sort_order": 3,
        "description": "Pharmacy chains. No limits.",
        "price_monthly": Decimal("5000"), "price_yearly": Decimal("50000"),
        "max_outlets": None, "max_users": None, "max_medicines": None,
    },
]


def create_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for spec in DEFAULT_PLANS:
        Plan.objects.get_or_create(slug=spec["slug"], defaults=spec)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]

    operations = [migrations.RunPython(create_plans, migrations.RunPython.noop)]
