"""Writes the audit log from model signals and sign-in signals.

Saves through the ORM are caught; queryset.update() and bulk_create() aren't (the code uses them only for
system bookkeeping such as stock balances and running numbers)."""
import json
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models import FileField
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .context import client_ip, current, is_paused
from .models import AuditEntry
from .registry import AUDITED, NEVER, SECRET, rule_for

MAX_TEXT = 500
MAX_SNAPSHOT = 30
DOTS = "••••••"


def _fields(model, rule):
    return [f for f in model._meta.concrete_fields if f.name not in NEVER and f.name not in rule.ignore]


def _raw(value):
    """A comparable form: 1.50 and Decimal('1.5') match, a missing value matches an empty one."""
    if value in (None, ""):
        return ""
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _show(field, value):
    """What a value looks like in the log: names for links, labels for choices, local times, dots for secrets."""
    if field.name in SECRET:
        return DOTS if value else ""
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return value
    if field.is_relation:
        related = field.related_model._base_manager.filter(pk=value).first()
        return str(related)[:MAX_TEXT] if related is not None else f"#{value}"
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(value, Decimal) and getattr(field, "decimal_places", None) is not None:
        return f"{value:.{field.decimal_places}f}"
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if timezone.is_aware(value) else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(field, FileField):
        return str(getattr(value, "name", value) or "")
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value)[:MAX_TEXT]


def _list_change(before, after):
    """A list field (a role's permissions): what was taken away and what was added, not two long lists."""
    before, after = set(before or []), set(after or [])
    return ["− " + ", ".join(sorted(before - after)) if before - after else "", "+ " + ", ".join(sorted(after - before)) if after - before else ""]


def snapshot(instance, rule):
    """The record as it is: its filled-in fields, for "added" and "deleted" entries."""
    values = {}
    for f in _fields(type(instance), rule):
        shown = _show(f, getattr(instance, f.attname))
        if shown not in ("", None, False):
            values[f.name] = shown
        if len(values) >= MAX_SNAPSHOT:
            break
    return values


def write(*, action, tenant_id, user=None, model="", object_id="", object_repr="", changes=None, request=None):
    """One audit line. Uses the current request's person and address unless given."""
    if tenant_id is None:
        return None
    actor = current()
    if user is None and actor is not None:
        user = actor.user
    ip, agent, path = (actor.ip, actor.agent, actor.path) if actor else (None, "", "")
    if request is not None:
        ip, agent, path = client_ip(request), request.META.get("HTTP_USER_AGENT", "")[:200], request.path[:200]
    name = (user.get_full_name() or user.email) if user is not None else ""
    return AuditEntry.all_objects.create(
        tenant_id=tenant_id, action=action, user=user if user is not None and user.pk else None, user_name=name[:150], model=model,
        object_id=str(object_id)[:40], object_repr=str(object_repr)[:200], changes=changes or {}, ip=ip, user_agent=agent, path=path)


def _record(instance, action, changes):
    rule = rule_for(type(instance))
    name = rule.name(instance) if rule and rule.name else instance
    write(action=action, tenant_id=getattr(instance, "tenant_id", None), model=instance._meta.label_lower, object_id=instance.pk,
          object_repr=name, changes=changes)


def _only_quiet(rule, update_fields):
    return update_fields is not None and set(update_fields) - {"updated_at"} <= set(rule.quiet) | set(rule.ignore)


@receiver(pre_save)
def remember_before(sender, instance, raw=False, update_fields=None, **kwargs):
    rule = rule_for(sender)
    if raw or rule is None or instance.pk is None or is_paused() or _only_quiet(rule, update_fields):
        return
    fields = _fields(sender, rule)
    instance._audit_before = sender._base_manager.filter(pk=instance.pk).values(*[f.attname for f in fields]).first()


@receiver(post_save)
def record_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    rule = rule_for(sender)
    if raw or rule is None or is_paused() or (rule.only_if and not rule.only_if(instance)):
        return
    actor, key = current(), (sender._meta.label_lower, str(instance.pk))
    if created:
        if not rule.creates:
            return
        if actor is not None:
            actor.created.add(key)
        _record(instance, AuditEntry.Action.CREATE, snapshot(instance, rule))
        return
    before = instance.__dict__.pop("_audit_before", None)
    if before is None or (actor is not None and key in actor.created):
        return  # nothing to compare, or the finishing touches on a record made in this same request
    changes = {}
    for f in _fields(sender, rule):
        old, new = before.get(f.attname), getattr(instance, f.attname)
        if _raw(old) == _raw(new):
            continue
        if isinstance(old, list) or isinstance(new, list):
            changes[f.name] = _list_change(old, new)
        else:
            changes[f.name] = [_show(f, old), _show(f, new)]
    if changes:
        _record(instance, AuditEntry.Action.UPDATE, changes)


@receiver(post_delete)
def record_delete(sender, instance, **kwargs):
    rule = rule_for(sender)
    if rule is None or is_paused() or (rule.only_if and not rule.only_if(instance)):
        return
    _record(instance, AuditEntry.Action.DELETE, snapshot(instance, rule))


@receiver(m2m_changed)
def record_links(sender, instance, action, model, pk_set, reverse, **kwargs):
    """Many-to-many changes, e.g. which shifts or outlets an employee has."""
    if reverse or action not in ("post_add", "post_remove") or not pk_set or is_paused():
        return
    rule = rule_for(type(instance))
    if rule is None:
        return
    field = next((f for f in type(instance)._meta.many_to_many if f.remote_field.through is sender and f.name in rule.m2m), None)
    if field is None:
        return
    names = ", ".join(sorted(str(obj) for obj in model._base_manager.filter(pk__in=pk_set)))
    change = ["", "+ " + names] if action == "post_add" else ["− " + names, ""]
    _record(instance, AuditEntry.Action.UPDATE, {field.name: change})


# ---- Signing in and out ----------------------------------------------------------------

@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    if not is_paused():
        write(action=AuditEntry.Action.LOGIN, tenant_id=user.tenant_id, user=user, model="accounts.user", object_id=user.pk,
              object_repr=user.email, request=request)


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if user is not None and not is_paused():
        write(action=AuditEntry.Action.LOGOUT, tenant_id=user.tenant_id, user=user, model="accounts.user", object_id=user.pk,
              object_repr=user.email, request=request)


@receiver(user_login_failed)
def record_failed_login(sender, credentials, request=None, **kwargs):
    """A wrong password for a real login of a pharmacy (unknown names have no pharmacy to log them under)."""
    from django.db.models import Q

    from apps.accounts.models import User

    name = (credentials.get("username") or credentials.get("email") or "").strip()
    if not name or is_paused():
        return
    user = User.objects.filter(Q(email__iexact=name) | Q(phone=name), tenant__isnull=False).first()
    if user is not None:
        write(action=AuditEntry.Action.LOGIN_FAILED, tenant_id=user.tenant_id, user=user, model="accounts.user", object_id=user.pk,
              object_repr=user.email, request=request)


__all__ = ["AUDITED", "snapshot", "write"]
