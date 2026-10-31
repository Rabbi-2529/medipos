from django import template
from django.utils.html import format_html

from apps.tenants.models import AccessState

register = template.Library()


def state_badge_html(state):
    state = AccessState(state)
    return format_html('<span class="state-badge state-{}">{}</span>', state.value, state.label)


@register.simple_tag
def state_badge(tenant):
    return state_badge_html(tenant.access_state())


@register.filter
def limit_text(limit):
    return "∞" if limit is None else limit
