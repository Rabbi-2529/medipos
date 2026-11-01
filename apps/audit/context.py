"""Who is doing things right now: set per request by AuditMiddleware, empty in management commands (shown as "System")."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from django.conf import settings


@dataclass
class Actor:
    user: object = None
    ip: str | None = None
    agent: str = ""
    path: str = ""
    paused: bool = False
    created: set = field(default_factory=set)  # records added during this request: their finishing saves aren't "changes"


_actor = ContextVar("audit_actor", default=None)


def current():
    return _actor.get()


def client_ip(request):
    """The visitor's address; behind a reverse proxy (BEHIND_PROXY=True) the first X-Forwarded-For hop."""
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR") or None


def actor_for(request):
    user = getattr(request, "user", None)
    return Actor(user=user if user is not None and user.is_authenticated else None, ip=client_ip(request),
                 agent=request.META.get("HTTP_USER_AGENT", "")[:200], path=request.path[:200])


@contextmanager
def acting(actor):
    token = _actor.set(actor)
    try:
        yield actor
    finally:
        _actor.reset(token)


@contextmanager
def paused():
    """Nothing done inside is written to the audit log (demo data, bulk jobs that log a summary themselves)."""
    actor = current() or Actor()
    previous = actor.paused
    actor.paused = True
    token = _actor.set(actor)
    try:
        yield
    finally:
        actor.paused = previous
        _actor.reset(token)


def is_paused():
    actor = current()
    return bool(actor and actor.paused)


class AuditMiddleware:
    """Remembers who is making this request, for the audit log. Must come after AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with acting(actor_for(request)):
            return self.get_response(request)
