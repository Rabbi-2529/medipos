from django.db import migrations

MANAGER = ("message.list", "message.create", "message.edit", "loyalty.list", "loyalty.edit")


def grant(apps, schema_editor):
    """Template "Manager" roles run messages, campaigns and loyalty (owners see everything anyway)."""
    Role = apps.get_model("people", "Role")
    for role in Role.objects.filter(template_key="manager"):
        missing = [code for code in MANAGER if code not in role.permissions]
        if missing:
            role.permissions = sorted([*role.permissions, *missing])
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("messaging", "0001_initial"), ("people", "0005_employee_shifts")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
