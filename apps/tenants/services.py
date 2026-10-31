from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.tenancy import tenant_context
from apps.outlets.models import Outlet
from apps.setup.services import provision_tenant

from .models import Tenant, TenantEvent


def unique_slug(name):
    base = slugify(name)[:40].strip("-") or "pharmacy"
    slug, n = base, 2
    while Tenant.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


@transaction.atomic
def register_pharmacy(*, business_name, business_phone, address, plan, owner_name, email, phone, password,
                      trial_days=None, created_by=None, now=None):
    """Pharmacy on a free trial + its owner + a first outlet.

    Used by self-signup and by platform admins (`created_by`) adding a pharmacy for a customer.
    """
    now = now or timezone.now()
    trial_days = trial_days or settings.TRIAL_DAYS
    tenant = Tenant.objects.create(
        name=business_name,
        slug=unique_slug(business_name),
        phone=business_phone,
        email=email,
        address=address,
        plan=plan,
        status=Tenant.Status.TRIAL,
        trial_ends_at=now + timedelta(days=trial_days),
    )
    first_name, _sep, last_name = owner_name.strip().partition(" ")
    owner = User.objects.create_user(
        email=email,
        password=password,
        phone=phone,
        first_name=first_name,
        last_name=last_name.strip(),
        tenant=tenant,
        is_owner=True,
    )
    with tenant_context(tenant):
        Outlet.objects.create(name=_("Main branch"), code="0001", phone=business_phone, email=email, address=address)
    provision_tenant(tenant)
    params = {"plan": plan.name, "days": trial_days}
    if created_by:
        message = _("Added by ThingBD on %(plan)s with a %(days)s-day free trial") % params
    else:
        message = _("Signed up for %(plan)s with a %(days)s-day free trial") % params
    TenantEvent.objects.create(tenant=tenant, kind=TenantEvent.Kind.SIGNUP, message=message, actor=created_by or owner)
    return tenant, owner
