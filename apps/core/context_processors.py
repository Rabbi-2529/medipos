from django.conf import settings

from apps.billing.services import subscription_banner

from .menu import build_menu, flatten_menu


def app_context(request):
    ctx = {
        "APP_NAME": settings.APP_NAME,
        "APP_COMPANY": settings.APP_COMPANY,
        "APP_PRODUCT": settings.APP_PRODUCT,
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
        ctx["allowed_outlets"] = getattr(request, "allowed_outlets", [])
    return ctx
