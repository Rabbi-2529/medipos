from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "apps.core",
    "apps.tenants",
    "apps.accounts",
    "apps.billing",
    "apps.outlets",
    "apps.console",
    "apps.setup",
    "apps.people",
    "apps.accounting",
    "apps.items",
    "apps.catalog",
    "apps.stock",
    "apps.suppliers",
    "apps.purchases",
    "apps.customers",
    "apps.sales",
    "apps.inventory",
    "apps.reports",
    "apps.hr",
    "apps.messaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must come after AuthenticationMiddleware: it reads request.user.tenant.
    "apps.core.middleware.TenantMiddleware",
    "apps.core.middleware.UserLanguageMiddleware",
    # Locks out pharmacies whose trial/subscription has lapsed. Needs request.tenant.
    "apps.billing.middleware.SubscriptionGateMiddleware",
    "apps.outlets.middleware.CurrentOutletMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.app_context",
            ],
        },
    },
]

# An empty DATABASE_URL (as in .env.example) also falls back to SQLite.
DATABASES = {
    "default": env.db_url_config(env("DATABASE_URL", default="") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrPhoneBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en"
# Each language is named in itself, so people can always find their own.
LANGUAGES = [("en", "English"), ("bn", "বাংলা")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

APP_COMPANY = "ThingBD"
APP_PRODUCT = "MediPOS"
APP_NAME = f"{APP_COMPANY} {APP_PRODUCT}"

# Behind nginx or another reverse proxy the visitor's address arrives in X-Forwarded-For
# (attendance checks it against the shop's networks). Only turn on when a proxy sets that header.
BEHIND_PROXY = env.bool("BEHIND_PROXY", default=False)

# Messages. Links in SMS and email (a customer's bill) start with SITE_URL when there is no request to take it from.
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MediPOS <no-reply@medipos.local>")
# The platform's SMS account, used by pharmacies on MediPOS SMS credits. With no URL, SMS are only logged (development).
# The URL holds {to}, {message}, {sender} and {api_key}; SUCCESS_TEXT is something the provider's answer contains on success.
SMS_GATEWAY_URL = env("SMS_GATEWAY_URL", default="")
SMS_GATEWAY_METHOD = env("SMS_GATEWAY_METHOD", default="GET")
SMS_GATEWAY_API_KEY = env("SMS_GATEWAY_API_KEY", default="")
SMS_GATEWAY_SENDER = env("SMS_GATEWAY_SENDER", default="")
SMS_GATEWAY_SUCCESS_TEXT = env("SMS_GATEWAY_SUCCESS_TEXT", default="")
# True: messages go out right after the page saves them (up to MESSAGES_SEND_NOW_LIMIT a request).
# False: they wait for `manage.py send_messages`, run every minute (which also sends the daily alerts and due reminders).
MESSAGES_SEND_NOW = env.bool("MESSAGES_SEND_NOW", default=True)
MESSAGES_SEND_NOW_LIMIT = env.int("MESSAGES_SEND_NOW_LIMIT", default=25)

# SaaS subscription rules
TRIAL_DAYS = env.int("TRIAL_DAYS", default=14)
SUBSCRIPTION_GRACE_DAYS = env.int("SUBSCRIPTION_GRACE_DAYS", default=3)
SUBSCRIPTION_PAYMENT_INSTRUCTIONS = env(
    "SUBSCRIPTION_PAYMENT_INSTRUCTIONS",
    default="Contact ThingBD support to renew or upgrade your subscription.",
)
