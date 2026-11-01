import json
from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import PASSWORD, add_staff, make_medicine, make_pharmacy
from apps.customers.models import Customer
from apps.hr.models import Shift
from apps.messaging.services import message_settings
from apps.outlets.models import Outlet
from apps.people.models import Employee, Role
from apps.setup.services import next_code
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from .context import paused
from .models import AuditEntry

A = AuditEntry.Action


def customer_form(**overrides):
    data = {"name": "Rahim", "phone": "01712345678", "email": "", "address": "", "credit_limit": "", "default_discount": "0",
            "opening_balance": "0", "notes": ""}
    data.update(overrides)
    return data


class AuditTrailTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def entries(self, **filters):
        return list(AuditEntry.all_objects.filter(tenant=self.tenant, **filters).order_by("id"))

    def test_add_change_and_delete_are_written_with_who_and_where(self):
        self.client.post(reverse("customers:create"), customer_form(), REMOTE_ADDR="103.4.5.6")
        with tenant_context(self.tenant):
            rahim = Customer.objects.get()
        added = self.entries(model="customers.customer")[0]
        self.assertEqual((added.action, added.user, added.ip, added.object_repr), (A.CREATE, self.owner, "103.4.5.6", "Rahim · 01712345678"))
        self.assertEqual(added.changes["phone"], "01712345678")

        self.client.post(reverse("customers:edit", args=[rahim.pk]), customer_form(name="Rahim Uddin", credit_limit="5000"))
        self.client.post(reverse("customers:edit", args=[rahim.pk]), customer_form(name="Rahim Uddin", credit_limit="5000.00"))  # no change
        changed = self.entries(model="customers.customer", action=A.UPDATE)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].changes, {"name": ["Rahim", "Rahim Uddin"], "credit_limit": ["", "5000.00"]})

        with tenant_context(self.tenant):
            Customer.objects.get().delete()
        deleted = self.entries(model="customers.customer", action=A.DELETE)[0]
        self.assertEqual((deleted.changes["name"], deleted.user), ("Rahim Uddin", None))  # outside a request: the system

    def test_a_sale_is_one_line_and_running_numbers_are_not_logged(self):
        with tenant_context(self.tenant):
            outlet, cash = Outlet.objects.get(), PaymentAccount.objects.get(name="Cash")
            napa = make_medicine(self.tenant, "Napa", sale_price="10.00")
            batch = get_or_create_batch(napa, "B1", timezone.localdate() + timedelta(days=300), Decimal("5"))
            post_movement(outlet=outlet, batch=batch, quantity=50, kind=StockMovement.Kind.OPENING)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        self.client.post(reverse("sales:pos_checkout"), json.dumps({"lines": [{"medicine": napa.pk, "qty": 2}],
                                                                     "payments": [{"account": cash.pk, "amount": 20}]}),
                         content_type="application/json")
        sales = self.entries(model="sales.sale")
        self.assertEqual([(e.action, e.changes.get("total")) for e in sales], [(A.CREATE, "20.00")])
        self.assertFalse(self.entries(model="setup.sequence"))
        next_code(self.tenant, "invoice")
        self.assertFalse(self.entries(model="setup.sequence"))  # bumping the number is the system's business

    def test_secrets_lists_and_links(self):
        with tenant_context(self.tenant):
            ms = message_settings()
            ms.sms_api_key = "KEY-1"
            ms.save()
            role = Role.objects.get(template_key="cashier")
            role.permissions = sorted({*role.permissions, "report.list"} - {"pos.create"})
            role.save()
            day = Shift.objects.create(name="Day", start=time(9, 0), end=time(17, 0))
            employee = Employee.objects.get(user=self.owner)
            employee.shifts.add(day)
        key = self.entries(model="messaging.messagesettings", action=A.UPDATE)[0]
        self.assertEqual(key.changes["sms_api_key"], ["", "••••••"])
        self.assertNotIn("KEY-1", json.dumps([e.changes for e in self.entries()]))
        perms = self.entries(model="people.role", action=A.UPDATE)[0]
        self.assertEqual(perms.changes["permissions"], ["− pos.create", "+ report.list"])
        self.assertEqual(self.entries(model="people.employee", action=A.UPDATE)[0].changes, {"shifts": ["", "+ " + str(day)]})

    def test_sign_in_out_and_wrong_password(self):
        self.client.logout()
        self.assertEqual(self.entries(action=A.LOGOUT)[0].user, self.owner)
        self.client.post(reverse("accounts:login"), {"username": self.owner.email, "password": "wrong"}, REMOTE_ADDR="8.8.8.8")
        failed = self.entries(action=A.LOGIN_FAILED)[0]
        self.assertEqual((failed.user, failed.ip), (self.owner, "8.8.8.8"))
        self.client.post(reverse("accounts:login"), {"username": self.owner.email, "password": PASSWORD})
        self.assertEqual(self.entries(action=A.LOGIN)[-1].user, self.owner)

    def test_paused_writes_nothing(self):
        with paused(), tenant_context(self.tenant):
            Customer.objects.create(name="Quiet")
        self.assertFalse(self.entries(model="customers.customer"))

    def test_pages_and_who_may_see_them(self):
        self.client.post(reverse("customers:create"), customer_form())
        other, _owner = make_pharmacy("Rival Pharma")
        with tenant_context(other):
            Customer.objects.create(name="Not yours")
        data = self.client.get(reverse("audit:data"), {"draw": 1, "model": "customers.customer"}).json()
        self.assertEqual(data["recordsTotal"], 1)
        self.assertIn("Rahim", str(data["data"]))
        self.assertNotIn("Not yours", str(data["data"]))
        entry = self.entries(model="customers.customer")[0]
        with tenant_context(self.tenant):
            rahim = Customer.objects.get()
        page = self.client.get(reverse("audit:log"), {"model": "customers.customer", "object": rahim.pk})
        self.assertContains(page, "History of Customer “Rahim · 01712345678”")
        detail = self.client.get(reverse("audit:entry", args=[entry.pk]))
        self.assertContains(detail, "01712345678")
        self.assertContains(detail, reverse("customers:detail", args=[rahim.pk]))
        self.assertContains(self.client.get(reverse("customers:detail", args=[rahim.pk])), "History")

        with tenant_context(self.tenant):
            manager_role = Role.objects.get(template_key="manager")
        self.assertNotIn("audit.list", manager_role.permissions)
        self.client.force_login(add_staff(self.tenant, "m@x.test", manager_role.permissions, all_outlets=True))
        self.assertEqual(self.client.get(reverse("audit:log")).status_code, 403)
        self.client.force_login(add_staff(self.tenant, "a@x.test", ["audit.list"]))
        self.assertEqual(self.client.get(reverse("audit:log")).status_code, 200)


class BranchAuditTests(TestCase):
    """Head office audits a branch: figures, flags, checklist, findings, sign-off, report."""

    def setUp(self):
        from apps.sales.models import CashRegister
        from apps.sales.services import complete_sale, start_register

        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)
        with tenant_context(self.tenant):
            self.main = Outlet.objects.get()
            self.branch = Outlet.objects.create(code="0002", name="Gulshan branch")
            cash = PaymentAccount.objects.get(name="Cash")
            napa = make_medicine(self.tenant, "Napa", purchase_price="8.00", sale_price="10.00")
            batch = get_or_create_batch(napa, "B1", timezone.localdate() + timedelta(days=300), Decimal("8"))
            post_movement(outlet=self.branch, batch=batch, quantity=100, kind=StockMovement.Kind.OPENING)
            old = get_or_create_batch(napa, "OLD", timezone.localdate() - timedelta(days=5), Decimal("8"))
            post_movement(outlet=self.branch, batch=old, quantity=4, kind=StockMovement.Kind.OPENING)  # expired, still on the shelf
            register = start_register(self.owner, self.branch, "500")
            complete_sale(tenant=self.tenant, outlet=self.branch, user=self.owner, register=register, cart_discount="30",
                          lines=[{"medicine": napa.pk, "qty": 20, "batch": batch.pk}], payments=[{"account": cash.pk, "amount": "170"}])
            CashRegister.objects.filter(pk=register.pk).update(closed_at=timezone.now(), expected_cash=Decimal("670"),
                                                               counted_cash=Decimal("620"))  # 50 short

    def test_audit_from_start_to_sign_off(self):
        today = timezone.localdate()
        response = self.client.post(reverse("audit:branches"), {"outlet": self.branch.pk, "start": today - timedelta(days=1), "end": today})
        from .models import AuditCheck, BranchAudit

        audit = BranchAudit.all_objects.get(tenant=self.tenant)
        self.assertRedirects(response, reverse("audit:branch", args=[audit.pk]))
        self.assertEqual((audit.number, audit.figures["sales"]["total"], audit.figures["cash"]["short"]), ("AUD-0001", "170.00", "50.00"))
        self.assertEqual(audit.figures["books"]["doc_sales"], audit.figures["books"]["sales"])  # bills match the books
        flags = dict(AuditCheck.all_objects.filter(audit=audit).values_list("key", "flag"))
        self.assertEqual((flags["cash"], flags["discounts"], flags["expired"], flags["books"], flags["count"]),
                         ("warn", "warn", "warn", "ok", "warn"))  # 30 off 200 is 15%; no stock count yet

        page = self.client.get(reverse("audit:branch", args=[audit.pk]))
        self.assertContains(page, "Cash registers balanced")
        self.assertContains(page, "Gulshan branch")

        url = reverse("audit:branch", args=[audit.pk])
        self.client.post(url, {"action": "finding", "finding-title": "Cash short ৳50", "finding-severity": "high",
                               "finding-detail": "Register closed 50 short.", "finding-action": "Cashier to explain"})
        self.client.post(url, {"action": "sign", "sign-verdict": "watch", "sign-summary": "Cash handling needs care."})
        audit.refresh_from_db()
        self.assertTrue(audit.is_open)  # every check needs an answer first
        answers = {f"result-{key}": "ok" for key in flags}
        answers["result-cash"], answers["note-cash"] = "issue", "50 short on 1 register"
        self.client.post(url, {"action": "checks", **answers})
        self.client.post(url, {"action": "sign", "sign-verdict": "watch", "sign-summary": "Cash handling needs care."})
        audit.refresh_from_db()
        self.assertEqual((audit.status, audit.verdict, audit.signed_by), ("done", "watch", self.owner))
        self.client.post(url, {"action": "refresh"})  # locked now
        report = self.client.get(reverse("audit:branch_print", args=[audit.pk]))
        for text in ("Branch audit report", "Cash short ৳50", "50 short on 1 register", "Needs improvement"):
            self.assertContains(report, text)
        self.assertTrue(AuditEntry.all_objects.filter(tenant=self.tenant, model="audit.branchaudit", action=A.UPDATE).exists())

    def test_who_may_audit(self):
        with tenant_context(self.tenant):
            manager_role = Role.objects.get(template_key="manager")
        self.assertFalse(any(code.startswith("branch_audit.") for code in manager_role.permissions))
        self.client.force_login(add_staff(self.tenant, "viewer@x.test", ["branch_audit.list"]))
        self.assertEqual(self.client.get(reverse("audit:branches")).status_code, 200)
        response = self.client.post(reverse("audit:branches"), {"outlet": self.branch.pk, "start": "2026-10-01", "end": "2026-10-31"})
        self.assertEqual(response.status_code, 404)  # looking only, no starting
