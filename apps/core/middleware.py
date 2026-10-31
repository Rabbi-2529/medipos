from django.utils import translation

from .tenancy import reset_current_tenant, set_current_tenant


class TenantMiddleware:
    """Activates the logged-in user's tenant for the rest of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        tenant = user.tenant if user is not None and user.is_authenticated else None
        request.tenant = tenant
        token = set_current_tenant(tenant)
        try:
            return self.get_response(request)
        finally:
            reset_current_tenant(token)


class UserLanguageMiddleware:
    """Logged-in people see their own language, else their pharmacy's default (must come after TenantMiddleware).

    Visitors who aren't logged in keep Django's normal cookie/browser choice (LocaleMiddleware).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            language = user.language
            tenant = getattr(request, "tenant", None)
            if not language and tenant is not None:
                from apps.setup.services import get_business_settings

                request._business_settings = get_business_settings(tenant)
                language = request._business_settings.default_language
            if language:
                translation.activate(language)
                request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
