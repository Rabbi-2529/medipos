import time

from django.core.management.base import BaseCommand, CommandError

from apps.core.tenancy import tenant_context
from apps.messaging.services import deliver_waiting, run_daily
from apps.tenants.models import AccessState, Tenant


class Command(BaseCommand):
    help = ("Send waiting SMS/WhatsApp/email and, once a day per pharmacy (from its set hour), the stock alert and due reminders. "
            "Run it every minute from cron, or keep it running with --loop.")

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Slug of one pharmacy (default: all of them).")
        parser.add_argument("--loop", type=int, default=0, metavar="SECONDS", help="Keep running, waking up every SECONDS.")

    def handle(self, *args, **options):
        tenants = Tenant.objects.all()
        if options["tenant"]:
            tenants = tenants.filter(slug=options["tenant"])
            if not tenants:
                raise CommandError(f"No pharmacy with slug {options['tenant']!r}.")
        while True:
            self.run_once(tenants)
            if not options["loop"]:
                break
            time.sleep(options["loop"])

    def run_once(self, tenants):
        for tenant in tenants:
            if tenant.access_state() in (AccessState.EXPIRED, AccessState.SUSPENDED):
                continue
            with tenant_context(tenant):
                done = run_daily()
                sent = deliver_waiting(limit=500)
            if done:
                self.stdout.write(f"{tenant.name}: {len(done['alerts'])} stock alerts, {len(done['reminders'])} due reminders queued.")
            if sent:
                ok = sum(1 for m in sent if m.status == "sent")
                self.stdout.write(f"{tenant.name}: {ok} of {len(sent)} messages sent.")
