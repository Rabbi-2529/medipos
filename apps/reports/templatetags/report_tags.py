from django import template
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils.html import format_html

from apps.setup.templatetags.shop import money, shop_date

from ..engine import Link

register = template.Library()


@register.simple_tag(takes_context=True)
def cell(context, value, kind):
    """One report cell, formatted by its column's kind."""
    if value is None or value == "":
        return "—"
    if isinstance(value, Link):
        return format_html('<a href="{}">{}</a>', value.url, value.text)
    if kind == "money":
        return money(context, value)
    if kind == "int":
        return intcomma(value)
    if kind == "pct":
        return f"{value}%"
    if kind == "date":
        return shop_date(context, value)
    return value


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None
