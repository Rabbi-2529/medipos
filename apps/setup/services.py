from decimal import Decimal

from django.db import transaction
from django.utils.dateformat import format as date_format

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.items.models import Category, Unit
from apps.people.defaults import DEFAULT_ROLES
from apps.people.models import Employee, Role

from .models import BusinessSettings, Sequence

DEFAULT_PAYMENT_ACCOUNTS = [
    ("Cash", PaymentAccount.Kind.CASH, ""),
    ("bKash", PaymentAccount.Kind.MOBILE, "bKash"),
    ("Nagad", PaymentAccount.Kind.MOBILE, "Nagad"),
]
DEFAULT_UNITS = ["Piece", "Strip", "Box", "Bottle", "Tube", "Vial", "Ampoule", "Sachet", "Pack", "Jar"]
DEFAULT_CATEGORIES = [
    "Medicine", "Vitamins & supplements", "Baby care", "Personal care", "Medical devices", "Surgical items", "Herbal",
]


def provision_tenant(tenant):
    """Starting data every pharmacy needs. Safe to run again (creates only what's missing)."""
    with tenant_context(tenant):
        BusinessSettings.objects.get_or_create()
        Sequence.objects.get_or_create(key="invoice", defaults={"prefix": "INV", "padding": 5})
        for order, (name, kind, provider) in enumerate(DEFAULT_PAYMENT_ACCOUNTS):
            PaymentAccount.objects.get_or_create(name=name, defaults={"kind": kind, "provider": provider, "sort_order": order})
        for key, spec in DEFAULT_ROLES.items():
            Role.objects.get_or_create(template_key=key, defaults={
                "name": spec["name"], "description": spec["description"], "permissions": spec["permissions"],
            })
        for user in tenant.users.all():
            Employee.objects.get_or_create(user=user, defaults={"all_outlets": user.is_owner})
        for name in DEFAULT_UNITS:
            Unit.objects.get_or_create(name=name)
        for name in DEFAULT_CATEGORIES:
            Category.objects.get_or_create(name=name)
        from apps.accounting.chart import ensure_chart

        ensure_chart(language=BusinessSettings.objects.get().default_language)


def get_business_settings(tenant):
    settings, _ = BusinessSettings.all_objects.get_or_create(tenant=tenant)
    return settings


def invoice_sequence(tenant):
    sequence, _ = Sequence.all_objects.get_or_create(tenant=tenant, key="invoice", defaults={"prefix": "INV", "padding": 5})
    return sequence


@transaction.atomic
def reserve_codes(tenant, key, count, prefix="", padding=5):
    """Take `count` consecutive document numbers for `key` in one locked step."""
    sequence, _ = Sequence.all_objects.select_for_update().get_or_create(
        tenant=tenant, key=key, defaults={"prefix": prefix, "padding": padding}
    )
    start = sequence.next_value
    sequence.next_value = start + count
    sequence.save(update_fields=["next_value", "updated_at"])
    return [sequence.format(value) for value in range(start, start + count)]


def next_code(tenant, key, prefix="", padding=5):
    """Take the next document number for `key`, e.g. next_code(t, "invoice") -> "INV00001"."""
    return reserve_codes(tenant, key, 1, prefix=prefix, padding=padding)[0]


def format_money(value, settings):
    amount = Decimal(value or 0)
    text = f"{abs(amount):,.{settings.decimal_places}f}"
    sign = "−" if amount < 0 and text.strip("0.,") else ""  # "−৳1,500.00", not "৳-1,500.00"
    if settings.currency_position == BusinessSettings.CurrencyPosition.AFTER:
        return f"{sign}{text}{settings.currency_symbol}"
    return f"{sign}{settings.currency_symbol}{text}"


def format_shop_date(value, settings):
    return date_format(value, settings.date_format) if value else ""


def document_language(settings, person_language=None):
    """The language invoices and quotations print in: the shop's fixed choice, else the person printing, else the shop default."""
    if settings.receipt_language in ("en", "bn"):
        return settings.receipt_language
    return (person_language or settings.default_language or "en")[:2]
