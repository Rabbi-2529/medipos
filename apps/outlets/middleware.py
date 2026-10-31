SESSION_KEY = "outlet_id"


class CurrentOutletMiddleware:
    """Sets request.outlet (the outlet this user is working in) and request.allowed_outlets.

    The choice is kept in the session; if it's missing or no longer allowed, the
    first allowed outlet is used.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.outlet = None
        request.allowed_outlets = []
        user = getattr(request, "user", None)
        if getattr(request, "tenant", None) is not None and user.is_authenticated:
            from apps.people.access import allowed_outlets

            outlets = list(allowed_outlets(user))
            chosen = request.session.get(SESSION_KEY)
            outlet = next((o for o in outlets if o.pk == chosen), None) or (outlets[0] if outlets else None)
            if outlet is not None and outlet.pk != chosen:
                request.session[SESSION_KEY] = outlet.pk
            request.allowed_outlets = outlets
            request.outlet = outlet
        return self.get_response(request)
