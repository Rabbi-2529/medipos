from django.http import JsonResponse
from django.shortcuts import redirect

# Pages a locked-out pharmacy can still reach.
ALWAYS_ALLOWED = frozenset({
    "billing:subscription",
    "accounts:logout",
    "accounts:profile",
    "accounts:password_change",
    "set_language",
    "core:healthz",
})


class SubscriptionGateMiddleware:
    """Sends staff of expired or suspended pharmacies to the subscription page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        tenant = getattr(request, "tenant", None)
        if tenant is None or tenant.has_access:
            return None
        match = request.resolver_match
        if match is not None and match.view_name in ALWAYS_ALLOWED:
            return None
        if request.path.startswith("/api/") or request.headers.get("HX-Request"):
            return JsonResponse({"detail": "Subscription inactive."}, status=402)
        return redirect("billing:subscription")
