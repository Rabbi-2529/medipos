from django.db import migrations, models


def copy_shift(apps, schema_editor):
    """Everyone keeps the shift they had, now as the first of their shifts."""
    Employee = apps.get_model("people", "Employee")
    through = Employee.shifts.through
    through.objects.bulk_create([through(employee_id=pk, shift_id=shift_id)
                                 for pk, shift_id in Employee.objects.filter(shift__isnull=False).values_list("pk", "shift_id")])


def copy_back(apps, schema_editor):
    Employee = apps.get_model("people", "Employee")
    for row in Employee.shifts.through.objects.order_by("shift__start"):
        Employee.objects.filter(pk=row.employee_id, shift__isnull=True).update(shift_id=row.shift_id)


class Migration(migrations.Migration):
    """One shift per person becomes any number (a split day, or a day and an evening shift)."""

    dependencies = [
        ('hr', '0003_shifts_and_office_networks'),
        ('people', '0004_employee_shift'),
    ]

    operations = [
        migrations.AlterField(
            model_name='employee',
            name='shift',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='+', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='employee',
            name='shifts',
            field=models.ManyToManyField(blank=True, related_name='employees', to='hr.shift', verbose_name='shifts'),
        ),
        migrations.RunPython(copy_shift, copy_back),
        migrations.RemoveField(model_name='employee', name='shift'),
    ]
