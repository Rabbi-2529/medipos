"""MediPOS on one shop computer: started by scripts/windows/medipos_desktop.py from the desktop shortcut.

Like production (no debug pages, WhiteNoise for files) but over plain http on this PC: browsers count
http://localhost as secure, so the installed app and the offline POS work without a certificate.
"""
from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, env

DEBUG = False
SECRET_KEY = env("SECRET_KEY")

PORT = env.int("MEDIPOS_PORT", default=8800)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])
SITE_URL = env("SITE_URL", default=f"http://localhost:{PORT}").rstrip("/")

# Files come straight from the apps' static folders, so an update needs no collectstatic.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]
WHITENOISE_USE_FINDERS = True
SERVE_MEDIA = True  # logos, letterheads and medicine photos; there is no nginx here

# The launcher's worker sends queued SMS and email once a minute, so a sale never waits on the gateway.
MESSAGES_SEND_NOW = env.bool("MESSAGES_SEND_NOW", default=False)

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):  # noqa: F405
    # The server's threads and the message worker share one file: wait for each other instead of failing.
    DATABASES["default"].setdefault("OPTIONS", {}).update({"timeout": 20, "transaction_mode": "IMMEDIATE"})  # noqa: F405

(BASE_DIR / "logs").mkdir(exist_ok=True)  # noqa: F405
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"file": {
        "class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR / "logs" / "medipos.log",  # noqa: F405
        "maxBytes": 2_000_000, "backupCount": 3, "encoding": "utf-8", "formatter": "plain",
    }},
    "root": {"handlers": ["file"], "level": "WARNING"},
    "loggers": {"waitress": {"level": "INFO"}},
}
