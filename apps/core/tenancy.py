"""Current-tenant context.

The active tenant lives in a ContextVar so it is isolated per request/thread/task.
`TenantMiddleware` sets it for web requests; background jobs and management
commands must wrap their work in `tenant_context(tenant)`.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_current_tenant = ContextVar("current_tenant", default=None)
_bypass = ContextVar("tenant_bypass", default=False)


class TenantMissingError(RuntimeError):
    """Raised when tenant-owned data is written without an active tenant."""


def get_current_tenant():
    return _current_tenant.get()


def set_current_tenant(tenant):
    return _current_tenant.set(tenant)


def reset_current_tenant(token):
    _current_tenant.reset(token)


def is_tenant_bypassed():
    return _bypass.get()


@contextmanager
def tenant_context(tenant):
    token = _current_tenant.set(tenant)
    try:
        yield tenant
    finally:
        _current_tenant.reset(token)


@contextmanager
def bypass_tenant():
    """See every tenant's rows. Platform-admin and maintenance code only."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)
