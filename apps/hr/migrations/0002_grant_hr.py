from django.db import migrations

MANAGER = ("attendance.list", "attendance.create", "salary.list", "salary.create")


def grant(apps, schema_editor):
    """Template "Manager" roles run attendance and payroll (owners see everything anyway)."""
    Role = apps.get_model("people", "Role")
    for role in Role.objects.filter(template_key="manager"):
        missing = [code for code in MANAGER if code not in role.permissions]
        if missing:
            role.permissions = sorted([*role.permissions, *missing])
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("hr", "0001_initial"), ("people", "0003_staff_without_login_and_pay")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
