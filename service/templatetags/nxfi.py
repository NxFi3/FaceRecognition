"""Formatting only; all authoritative amounts remain integer IRR on the server."""
from django import template
register = template.Library()


@register.filter
def money(value):
    try:
        return format(int(value), ',')
    except (ValueError, TypeError):
        return value
