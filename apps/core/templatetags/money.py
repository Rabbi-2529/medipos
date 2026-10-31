from django import template

from apps.core.money import format_taka

register = template.Library()


@register.filter
def taka(value):
    return format_taka(value)
