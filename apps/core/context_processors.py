from django.conf import settings
from django.utils.translation import gettext as _

from apps.billing.services import subscription_banner

from .menu import build_menu, flatten_menu


def js_texts():
    """Words shown by app.js (tables, dialogs), in the page's language."""
    return {
        "search": _("Search…"), "perPage": _("_MENU_ per page"), "info": _("_START_–_END_ of _TOTAL_"),
        "infoEmpty": _("No records"), "infoFiltered": _("(filtered from _MAX_)"), "zeroRecords": _("Nothing matches your search."),
        "emptyTable": _("No records yet."), "nothingHere": _("Nothing here yet."), "cancel": _("Cancel"), "confirm": _("Yes, continue"),
        "searching": _("Searching…"), "noResults": _("No results found"), "loadingMore": _("Loading more…"),
        "typeMore": _("Type to search"), "today": _("Today"), "clear": _("Clear"), "pickDate": _("Pick a date"),
    }


def app_context(request):
    ctx = {
        "APP_NAME": settings.APP_NAME,
        "APP_COMPANY": settings.APP_COMPANY,
        "APP_PRODUCT": settings.APP_PRODUCT,
        "JS_TEXTS": js_texts(),
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from apps.people.access import Can, user_permissions

        menu = build_menu(request)
        tenant = getattr(request, "tenant", None)
        ctx["nav_menu"] = menu
        ctx["nav_search"] = flatten_menu(menu)
        ctx["current_tenant"] = tenant
        ctx["subscription_banner"] = subscription_banner(tenant) if tenant else None
        ctx["can"] = Can(user_permissions(user))
        ctx["current_outlet"] = getattr(request, "outlet", None)
        if tenant is not None:
            from apps.setup.services import get_business_settings

            # Date pickers show dates the way the pharmacy writes them (Django and flatpickr share d/m/Y/M).
            business = getattr(request, "_business_settings", None) or get_business_settings(tenant)
            ctx["DATE_FORMAT"] = business.date_format
        ctx["allowed_outlets"] = getattr(request, "allowed_outlets", [])
    return ctx
