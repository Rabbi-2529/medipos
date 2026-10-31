import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.core.tenancy import tenant_context
from apps.outlets.models import Outlet
from apps.tenants.models import Tenant

# One pharmacy per subscription situation, so every screen has something to show.
DEMO_TENANTS = [
    {
        "slug": "lazz-pharma-demo",
        "name": "Lazz Pharma (Demo)",
        "phone": "01700000000",
        "plan": "business",
        "state": "paid",
        "owner": {"email": "owner@lazzpharma.local", "phone": "01700000001", "first_name": "Lazz", "last_name": "Owner"},
    },
    {
        "slug": "green-life-demo",
        "name": "Green Life Pharmacy (Demo)",
        "phone": "01800000000",
        "plan": "starter",
        "state": "trial_ending",
        "owner": {"email": "owner@greenlife.local", "phone": "01800000001", "first_name": "Green", "last_name": "Owner"},
    },
    {
        "slug": "sunrise-demo",
        "name": "Sunrise Pharmacy (Demo)",
        "phone": "01900000000",
        "plan": "starter",
        "state": "expired",
        "owner": {"email": "owner@sunrise.local", "phone": "01900000001", "first_name": "Sunrise", "last_name": "Owner"},
    },
]


def _subscription_fields(state, now):
    today = timezone.localdate(now)
    if state == "paid":
        return {"status": Tenant.Status.ACTIVE, "trial_ends_at": now - timedelta(days=40), "paid_until": today + timedelta(days=25)}
    if state == "trial_ending":
        return {"status": Tenant.Status.TRIAL, "trial_ends_at": now + timedelta(days=2), "paid_until": None}
    return {"status": Tenant.Status.TRIAL, "trial_ends_at": now - timedelta(days=3), "paid_until": None}


class Command(BaseCommand):
    help = "Create a platform admin and three demo pharmacies (paid, trial ending, expired). Dev only; safe to re-run."

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD")
        if not password:
            raise CommandError("Set DEMO_PASSWORD in .env first (see .env.example).")

        self._ensure_user(
            "admin@medipos.local",
            password,
            first_name="Platform",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )
        now = timezone.now()
        for spec in DEMO_TENANTS:
            tenant, created = Tenant.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "phone": spec["phone"],
                    "plan": Plan.objects.get(slug=spec["plan"]),
                    **_subscription_fields(spec["state"], now),
                },
            )
            if not tenant.plan_id:  # pharmacies seeded before plans existed
                tenant.plan = Plan.objects.get(slug=spec["plan"])
                for field, value in _subscription_fields(spec["state"], now).items():
                    setattr(tenant, field, value)
                tenant.save()
            owner = self._ensure_user(spec["owner"]["email"], password, tenant=tenant, is_owner=True, **{
                k: v for k, v in spec["owner"].items() if k != "email"
            })
            if not owner.is_owner:
                owner.is_owner = True
                owner.save(update_fields=["is_owner"])
            with tenant_context(tenant):
                Outlet.objects.get_or_create(code="0001", defaults={"name": "Main branch", "phone": spec["phone"]})

        self.stdout.write(self.style.SUCCESS(
            "Demo ready: admin@medipos.local (platform), owner@lazzpharma.local (paid), "
            "owner@greenlife.local (trial ending), owner@sunrise.local (expired). Password = DEMO_PASSWORD from .env."
        ))

    def _ensure_user(self, email, password, **fields):
        user, created = User.objects.get_or_create(email=email, defaults=fields)
        if created:
            user.set_password(password)
            user.save()
        return user
