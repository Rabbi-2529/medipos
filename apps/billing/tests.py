from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.tenancy import tenant_context
from apps.outlets.models import Outlet
from apps.tenants.models import AccessState, Tenant, TenantEvent

from . import services
from .limits import PlanLimitReached, check_limit, usage_summary
from .models import Plan, SubscriptionPayment


def make_tenant(slug, **fields):
    fields.setdefault("plan", Plan.objects.get(slug="starter"))
    return Tenant.objects.create(name=slug.title(), slug=slug, **fields)


class AccessStateTests(TestCase):
    """Tenant.access_state() and TenantQuerySet.in_state() must always agree."""

    def test_every_case_matches_the_queryset(self):
        now = timezone.now()
        today = timezone.localdate(now)
        cases = {
            "trial-open": ({"status": "trial", "trial_ends_at": now + timedelta(days=3)}, AccessState.TRIAL),
            "trial-no-end": ({"status": "trial", "trial_ends_at": None}, AccessState.TRIAL),
            "trial-over": ({"status": "trial", "trial_ends_at": now - timedelta(minutes=1)}, AccessState.EXPIRED),
            "comp": ({"status": "active", "paid_until": None}, AccessState.ACTIVE),
            "paid-today": ({"status": "active", "paid_until": today}, AccessState.ACTIVE),
            "grace-1": ({"status": "active", "paid_until": today - timedelta(days=1)}, AccessState.GRACE),
            "grace-last": ({"status": "active", "paid_until": today - timedelta(days=3)}, AccessState.GRACE),
            "lapsed": ({"status": "active", "paid_until": today - timedelta(days=4)}, AccessState.EXPIRED),
            "suspended": ({"status": "suspended", "paid_until": today + timedelta(days=30)}, AccessState.SUSPENDED),
        }
        tenants = {slug: make_tenant(slug, **fields) for slug, (fields, _) in cases.items()}
        for slug, (_fields, expected) in cases.items():
            tenant = tenants[slug]
            with self.subTest(slug):
                self.assertEqual(tenant.access_state(now), expected)
                matching = [s for s in AccessState if Tenant.objects.in_state(s, now).filter(pk=tenant.pk).exists()]
                self.assertEqual(matching, [expected])

    def test_has_access_only_in_open_states(self):
        self.assertTrue(make_tenant("a", status="trial", trial_ends_at=timezone.now() + timedelta(days=1)).has_access)
        self.assertFalse(make_tenant("b", status="suspended").has_access)
        self.assertFalse(make_tenant("c", status="trial", trial_ends_at=timezone.now() - timedelta(days=1)).has_access)


class AddMonthsTests(TestCase):
    def test_month_end_is_clamped(self):
        self.assertEqual(services.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))

    def test_rolls_into_next_year(self):
        self.assertEqual(services.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))


class RecordPaymentTests(TestCase):
    def setUp(self):
        self.starter = Plan.objects.get(slug="starter")
        self.business = Plan.objects.get(slug="business")
        self.admin = User.objects.create_superuser("admin@x.test", "pw")
        self.now = timezone.now()
        self.today = timezone.localdate(self.now)

    def test_expired_trial_starts_paying_today(self):
        tenant = make_tenant("t", status="trial", trial_ends_at=self.now - timedelta(days=2))
        payment = services.record_payment(tenant, plan=self.business, months=1, method="bkash", actor=self.admin, now=self.now)
        tenant.refresh_from_db()
        self.assertEqual(payment.period_start, self.today)
        self.assertEqual(payment.period_end, services.add_months(self.today, 1) - timedelta(days=1))
        self.assertEqual(payment.amount, Decimal("2500"))
        self.assertEqual((tenant.status, tenant.plan, tenant.paid_until), ("active", self.business, payment.period_end))
        self.assertTrue(tenant.events.filter(kind=TenantEvent.Kind.PAYMENT).exists())

    def test_payment_during_trial_starts_when_trial_ends(self):
        trial_end = self.now + timedelta(days=5)
        tenant = make_tenant("t", status="trial", trial_ends_at=trial_end)
        payment = services.record_payment(tenant, plan=self.starter, months=1, method="cash", now=self.now)
        self.assertEqual(payment.period_start, timezone.localdate(trial_end))

    def test_renewal_extends_from_current_paid_until(self):
        tenant = make_tenant("t", status="active", paid_until=self.today + timedelta(days=10))
        payment = services.record_payment(tenant, plan=self.starter, months=3, method="cash", now=self.now)
        self.assertEqual(payment.period_start, self.today + timedelta(days=11))
        self.assertEqual(payment.amount, Decimal("3000"))

    def test_yearly_price_and_explicit_amount(self):
        tenant = make_tenant("t", status="active", paid_until=None)
        yearly = services.record_payment(tenant, plan=self.starter, months=12, method="bank", now=self.now)
        self.assertEqual(yearly.amount, Decimal("10000"))
        discounted = services.record_payment(tenant, plan=self.starter, months=1, method="bank", amount=Decimal("800"), now=self.now)
        self.assertEqual(discounted.amount, Decimal("800"))

    def test_payment_lifts_suspension(self):
        tenant = make_tenant("t", status="suspended", suspended_reason="Unpaid")
        services.record_payment(tenant, plan=self.starter, months=1, method="cash", now=self.now)
        tenant.refresh_from_db()
        self.assertEqual((tenant.status, tenant.suspended_reason), ("active", ""))
        self.assertEqual(tenant.access_state(self.now), AccessState.ACTIVE)


class LifecycleTests(TestCase):
    def setUp(self):
        self.starter = Plan.objects.get(slug="starter")
        self.business = Plan.objects.get(slug="business")

    def test_change_plan_refused_when_usage_does_not_fit(self):
        tenant = make_tenant("t", plan=self.business, status="active")
        for i in range(4):
            User.objects.create_user(f"u{i}@x.test", "pw", tenant=tenant)
        with self.assertRaisesMessage(services.SubscriptionError, "4 users (limit 3)"):
            services.change_plan(tenant, self.starter)
        tenant.refresh_from_db()
        self.assertEqual(tenant.plan, self.business)

    def test_change_plan_logs_event(self):
        tenant = make_tenant("t", plan=self.starter, status="active")
        services.change_plan(tenant, self.business)
        tenant.refresh_from_db()
        self.assertEqual(tenant.plan, self.business)
        self.assertEqual(tenant.events.get().kind, TenantEvent.Kind.PLAN_CHANGED)

    def test_extend_trial_adds_to_future_end_or_now(self):
        now = timezone.now()
        running = make_tenant("a", status="trial", trial_ends_at=now + timedelta(days=2))
        services.extend_trial(running, 7, now=now)
        running.refresh_from_db()
        self.assertEqual(running.trial_ends_at, now + timedelta(days=9))

        over = make_tenant("b", status="trial", trial_ends_at=now - timedelta(days=5))
        services.extend_trial(over, 7, now=now)
        over.refresh_from_db()
        self.assertEqual(over.trial_ends_at, now + timedelta(days=7))

    def test_extend_trial_only_for_trials(self):
        with self.assertRaises(services.SubscriptionError):
            services.extend_trial(make_tenant("t", status="active"), 7)

    def test_suspend_and_reactivate(self):
        trial = make_tenant("a", status="trial", trial_ends_at=timezone.now() + timedelta(days=3))
        services.suspend(trial, "Fraud check")
        trial.refresh_from_db()
        self.assertEqual((trial.status, trial.suspended_reason), ("suspended", "Fraud check"))
        with self.assertRaises(services.SubscriptionError):
            services.suspend(trial, "again")
        services.reactivate(trial)
        trial.refresh_from_db()
        self.assertEqual(trial.status, "trial")  # never paid -> back to trial dates

        paid = make_tenant("b", status="suspended", paid_until=timezone.localdate() + timedelta(days=5))
        services.reactivate(paid)
        paid.refresh_from_db()
        self.assertEqual(paid.status, "active")


class LimitTests(TestCase):
    def test_usage_and_limits(self):
        tenant = make_tenant("t", status="active")  # Starter: 1 outlet, 3 users
        User.objects.create_user("a@x.test", "pw", tenant=tenant)
        with tenant_context(tenant):
            Outlet.objects.create(name="Main", code="0001")
        usage = {row["key"]: row for row in usage_summary(tenant)}
        self.assertEqual((usage["users"]["used"], usage["users"]["limit"]), (1, 3))
        self.assertEqual((usage["outlets"]["used"], usage["outlets"]["limit"]), (1, 1))
        self.assertEqual(usage["medicines"]["used"], 0)
        check_limit(tenant, "users", adding=2)
        with self.assertRaises(PlanLimitReached):
            check_limit(tenant, "outlets")

    def test_unlimited_plan_never_blocks(self):
        tenant = make_tenant("t", plan=Plan.objects.get(slug="enterprise"))
        check_limit(tenant, "outlets", adding=500)


class BannerTests(TestCase):
    def test_banner_levels(self):
        now = timezone.now()
        today = timezone.localdate(now)
        fresh = make_tenant("a", status="trial", trial_ends_at=now + timedelta(days=10))
        ending = make_tenant("b", status="trial", trial_ends_at=now + timedelta(days=2))
        grace = make_tenant("c", status="active", paid_until=today - timedelta(days=1))
        self.assertIsNone(services.subscription_banner(fresh, now))
        self.assertEqual(services.subscription_banner(ending, now)["level"], "info")
        banner = services.subscription_banner(grace, now)
        self.assertEqual(banner["level"], "warning")
        self.assertIn("2 day(s)", banner["text"])


class SubscriptionGateTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant("t", status="trial", trial_ends_at=timezone.now() - timedelta(days=1))
        self.user = User.objects.create_user("staff@x.test", "pw", tenant=self.tenant)
        self.client.force_login(self.user)

    def test_expired_pharmacy_is_sent_to_subscription_page(self):
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("billing:subscription"))
        response = self.client.get(reverse("billing:subscription"))
        self.assertContains(response, "Your free trial has ended.")

    def test_profile_and_logout_still_work(self):
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))

    def test_htmx_calls_get_402(self):
        response = self.client.get(reverse("core:dashboard"), headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 402)

    def test_paying_restores_access(self):
        services.record_payment(self.tenant, plan=self.tenant.plan, months=1, method="cash")
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_open_pharmacy_and_platform_admin_are_not_gated(self):
        self.tenant.status = Tenant.Status.ACTIVE
        self.tenant.save()
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
        self.client.force_login(User.objects.create_superuser("admin@x.test", "pw"))
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_subscription_page_shows_only_own_payments(self):
        other = make_tenant("other", status="active")
        services.record_payment(other, plan=other.plan, months=1, method="cash", reference="OTHER-REF")
        services.record_payment(self.tenant, plan=self.tenant.plan, months=1, method="cash", reference="MINE")
        response = self.client.get(reverse("billing:subscription"))
        self.assertEqual(list(response.context["payments"]), list(SubscriptionPayment.objects.filter(tenant=self.tenant)))
