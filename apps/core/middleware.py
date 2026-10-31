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
