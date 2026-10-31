from django.conf import settings

from .menu import build_menu, flatten_menu


def app_context(request):
    ctx = {
        "APP_NAME": settings.APP_NAME,
        "APP_COMPANY": settings.APP_COMPANY,
        "APP_PRODUCT": settings.APP_PRODUCT,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        menu = build_menu(request)
        ctx["nav_menu"] = menu
        ctx["nav_search"] = flatten_menu(menu)
        ctx["current_tenant"] = getattr(request, "tenant", None)
    return ctx
