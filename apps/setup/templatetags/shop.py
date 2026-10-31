from django import template
from django.utils.translation import get_language

from apps.setup.services import format_money, format_shop_date, get_business_settings

register = template.Library()

BANGLA_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


@register.filter
def bn_digits(value, enabled=True):
    """123 -> ১২৩ when `enabled` and the page is in Bangla."""
    if not enabled or not (get_language() or "").startswith("bn"):
        return value
    return str(value).translate(BANGLA_DIGITS)


def _settings(context):
    request = context.get("request")
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return None
    if not hasattr(request, "_business_settings"):
        request._business_settings = get_business_settings(tenant)
    return request._business_settings


@register.simple_tag(takes_context=True)
def money(context, value):
    """{% money amount %} in the pharmacy's currency format."""
    settings = _settings(context)
    return format_money(value, settings) if settings else value


@register.simple_tag(takes_context=True)
def shop_date(context, value):
    """{% shop_date some_date %} in the pharmacy's date format."""
    settings = _settings(context)
    return format_shop_date(value, settings) if settings else value
