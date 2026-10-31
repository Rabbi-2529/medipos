from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = f"{settings.APP_NAME} platform admin"
admin.site.site_title = settings.APP_NAME

urlpatterns = [
    path("platform-admin/", admin.site.urls),
    path("platform/", include("apps.console.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("apps.accounts.urls")),
    path("settings/", include("apps.setup.urls")),
    path("outlets/", include("apps.outlets.urls")),
    path("accounts-book/", include("apps.accounting.urls")),
    path("people/", include("apps.people.urls")),
    path("medicines/", include("apps.items.urls")),
    path("", include("apps.tenants.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
