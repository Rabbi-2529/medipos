from django.db import migrations


def grant(apps, schema_editor):
    """Template "Manager" roles may see profit reports (owners see everything anyway)."""
    Role = apps.get_model("people", "Role")
    for role in Role.objects.filter(template_key="manager"):
        if "profit_report.list" not in role.permissions:
            role.permissions = sorted([*role.permissions, "profit_report.list"])
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("people", "0002_alter_role_uniq_role_name_per_tenant")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
