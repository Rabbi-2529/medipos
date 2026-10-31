from django.core.management.base import BaseCommand, CommandError

from apps.accounting.posting import rebuild
from apps.core.tenancy import tenant_context
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Re-post every automatic journal entry from the documents (sales, purchases, …). Manual entries are kept."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Slug of one pharmacy (default: all of them).")

    def handle(self, *args, **options):
        tenants = Tenant.objects.all()
        if options["tenant"]:
            tenants = tenants.filter(slug=options["tenant"])
            if not tenants:
                raise CommandError(f"No pharmacy with slug {options['tenant']!r}.")
        for tenant in tenants:
            with tenant_context(tenant):
                count = rebuild()
            self.stdout.write(f"{tenant.name}: {count} journal entries posted.")
