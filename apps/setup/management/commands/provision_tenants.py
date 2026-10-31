from django.core.management.base import BaseCommand

from apps.setup.services import provision_tenant
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Create missing starter data (settings, roles, payment accounts, staff profiles) for every pharmacy."

    def handle(self, *args, **options):
        count = 0
        for tenant in Tenant.objects.all():
            provision_tenant(tenant)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Provisioned {count} pharmacies."))
