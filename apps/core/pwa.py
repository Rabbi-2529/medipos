"""MediPOS as an installable app: the web app manifest, the service worker that keeps the POS opening without
internet, and the page shown for other pages while offline."""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

# Bump when the service worker's list of files changes, so tills drop their old copies.
CACHE_VERSION = "2"


def manifest(request):
    return JsonResponse({
        "name": settings.APP_NAME, "short_name": settings.APP_PRODUCT, "id": "/", "start_url": reverse("sales:pos"), "scope": "/",
        "display": "standalone", "background_color": "#ffffff", "theme_color": "#8b5cf6",
        "description": _("Pharmacy POS, stock and accounts. The POS keeps selling without internet."),
        "icons": [
            {"src": static("img/pwa-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("img/pwa-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("img/pwa-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")


@never_cache
def service_worker(request):
    response = render(request, "pwa/sw.js", {"version": CACHE_VERSION}, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    return render(request, "pwa/offline.html")
