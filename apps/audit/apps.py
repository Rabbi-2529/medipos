from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = _("Audit log")

    def ready(self):
        from . import recorder  # noqa: F401  (connects the signal handlers)
