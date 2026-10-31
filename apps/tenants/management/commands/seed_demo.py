import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.tenants.models import Tenant

DEMO_TENANTS = [
    {
        "slug": "lazz-pharma-demo",
        "name": "Lazz Pharma (Demo)",
        "phone": "01700000000",
        "owner": {"email": "owner@lazzpharma.local", "phone": "01700000001", "first_name": "Lazz", "last_name": "Owner"},
    },
    {
        "slug": "green-life-demo",
        "name": "Green Life Pharmacy (Demo)",
        "phone": "01800000000",
        "owner": {"email": "owner@greenlife.local", "phone": "01800000001", "first_name": "Green", "last_name": "Owner"},
    },
]


class Command(BaseCommand):
    help = "Create a platform admin and two demo pharmacies with owners (dev only). Safe to re-run."

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
        for spec in DEMO_TENANTS:
            tenant, _ = Tenant.objects.get_or_create(
                slug=spec["slug"],
                defaults={"name": spec["name"], "phone": spec["phone"], "status": Tenant.Status.ACTIVE},
            )
            self._ensure_user(spec["owner"]["email"], password, tenant=tenant, **{
                k: v for k, v in spec["owner"].items() if k != "email"
            })

        self.stdout.write(self.style.SUCCESS(
            "Demo ready: admin@medipos.local (platform), owner@lazzpharma.local and "
            "owner@greenlife.local (pharmacies). Password = DEMO_PASSWORD from .env."
        ))

    def _ensure_user(self, email, password, **fields):
        user, created = User.objects.get_or_create(email=email, defaults=fields)
        if created:
            user.set_password(password)
            user.save()
        return user
