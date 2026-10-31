from django.db import migrations


def grant(apps, schema_editor):
    """Existing "Manager" roles (created from the template) may see every cashier's register."""
    Role = apps.get_model("people", "Role")
    for role in Role.objects.filter(template_key="manager"):
        if "register.list" not in role.permissions:
            role.permissions = sorted([*role.permissions, "register.list"])
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("sales", "0001_initial"), ("people", "0002_alter_role_uniq_role_name_per_tenant")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
