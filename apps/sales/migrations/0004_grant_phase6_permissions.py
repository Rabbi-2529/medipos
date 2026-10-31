from django.db import migrations

MANAGER = ["promotion.list", "promotion.create", "promotion.edit", "promotion.delete",
           "adjustment.list", "adjustment.create", "quotation.list", "quotation.create", "quotation.delete"]
CASHIER = ["quotation.list", "quotation.create"]


def grant(apps, schema_editor):
    """Roles made from the templates get the Phase 6 screens (owners already see everything)."""
    Role = apps.get_model("people", "Role")
    for template, codes in (("manager", MANAGER), ("cashier", CASHIER)):
        for role in Role.objects.filter(template_key=template):
            missing = [c for c in codes if c not in role.permissions]
            if missing:
                role.permissions = sorted([*role.permissions, *missing])
                role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("sales", "0003_promotions_and_quotations"), ("people", "0002_alter_role_uniq_role_name_per_tenant")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
