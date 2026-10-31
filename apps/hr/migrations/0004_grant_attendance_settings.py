from django.db import migrations


def grant(apps, schema_editor):
    """Template "Manager" roles may set shifts and the shop's networks."""
    Role = apps.get_model("people", "Role")
    for role in Role.objects.filter(template_key="manager"):
        if "attendance.edit" not in role.permissions:
            role.permissions = sorted([*role.permissions, "attendance.edit"])
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("hr", "0003_shifts_and_office_networks"), ("people", "0004_employee_shift")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
