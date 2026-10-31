from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Test-only app with a concrete tenant-owned model (no migrations, synced by the test runner).
INSTALLED_APPS = [*INSTALLED_APPS, "apps.core.tests.testapp"]
