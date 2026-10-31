"""Plan limits: how much of each resource a pharmacy uses versus what its plan allows.

Phase 2+ screens call `check_limit()` before creating outlets, users or medicines.
"""
from django.apps import apps
from django.utils.translation import gettext_lazy as _

RESOURCE_LABELS = {
    "outlets": _("Outlets"),
    "users": _("Users"),
    "medicines": _("Medicines"),
}


class PlanLimitReached(Exception):
    pass


def count_usage(tenant, resource):
    if resource == "users":
        return tenant.users.filter(is_active=True).count()
    if resource == "outlets":
        return apps.get_model("outlets", "Outlet").all_objects.filter(tenant=tenant).count()
    if resource == "medicines":
        try:
            Medicine = apps.get_model("items", "Medicine")  # arrives in Phase 3
        except LookupError:
            return 0
        return Medicine.all_objects.filter(tenant=tenant).count()
    raise ValueError(f"Unknown resource {resource!r}")


def usage_summary(tenant, plan=None):
    plan = plan or tenant.plan
    rows = []
    for key, label in RESOURCE_LABELS.items():
        used = count_usage(tenant, key)
        limit = plan.limit(key) if plan else None
        rows.append({
            "key": key,
            "label": label,
            "used": used,
            "limit": limit,
            "percent": min(100, round(used * 100 / limit)) if limit else 0,
        })
    return rows


def over_limits(tenant, plan):
    """Resources where current usage would not fit in `plan`."""
    return [row for row in usage_summary(tenant, plan) if row["limit"] is not None and row["used"] > row["limit"]]


def check_limit(tenant, resource, adding=1):
    plan = tenant.plan
    limit = plan.limit(resource) if plan else None
    if limit is None:
        return
    used = count_usage(tenant, resource)
    if used + adding > limit:
        raise PlanLimitReached(
            _("Your %(plan)s plan allows %(limit)s %(resource)s. Upgrade to add more.")
            % {"plan": plan.name, "limit": limit, "resource": RESOURCE_LABELS[resource].lower()}
        )
