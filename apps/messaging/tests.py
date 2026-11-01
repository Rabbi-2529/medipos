import json
import urllib.error
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounting import reports as books
from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import add_staff, make_medicine, make_pharmacy
from apps.customers.models import Customer
from apps.outlets.models import Outlet
from apps.sales.services import SaleError, complete_sale, create_return, start_register
from apps.stock.models import StockMovement
from apps.stock.services import get_or_create_batch, post_movement

from . import loyalty
from .gateways import ConsoleGateway, HttpGateway, platform_sms_gateway, whatsapp_link
from .models import Campaign, Message, MessageSettings, SmsCredit
from .services import (
    MessageError, add_credits, after_sale, bill_url, credit_balance, due_customers, message_settings, preview, queue, read_bill_token,
    run_daily, send_campaign, send_due_reminders,
)
from .text import fill, normalize_phone, placeholders, sms_parts

TODAY = timezone.localdate()


class TextTests(SimpleTestCase):
    def test_phone_numbers(self):
        for raw in ("01712345678", "+8801712345678", "8801712345678", "01712-345678", "008801712345678", " 017 1234 5678 "):
            self.assertEqual(normalize_phone(raw), "8801712345678", raw)
        for raw in ("", "12345", "0171234567", "01212345678", "88017123456789", "abc"):
            self.assertEqual(normalize_phone(raw), "", raw)
        self.assertEqual(normalize_phone("+44 7911 123456"), "447911123456")

    def test_sms_parts(self):
        self.assertEqual(sms_parts(""), 0)
        self.assertEqual(sms_parts("a" * 160), 1)
        self.assertEqual(sms_parts("a" * 161), 2)
        self.assertEqual(sms_parts("a" * 306), 2)
        self.assertEqual(sms_parts("[" * 80), 1)  # two places each
        self.assertEqual(sms_parts("[" * 81), 2)
        self.assertEqual(sms_parts("ধ" * 70), 1)  # Bangla is Unicode
        self.assertEqual(sms_parts("ধ" * 71), 2)
        self.assertEqual(sms_parts("a" * 60 + "ধ"), 1)
        self.assertEqual(sms_parts("a" * 70 + "ধ"), 2)

    def test_fill(self):
        text = "{name}, bill {invoice}: {total}. {typo} {"
        self.assertEqual(fill(text, {"name": "Rahim", "invoice": "INV-1", "total": "TK 50", "unused": 1}), "Rahim, bill INV-1: TK 50. {typo} {")
        self.assertEqual(placeholders(text), ["name", "invoice", "total", "typo"])


class GatewayTests(SimpleTestCase):
    def test_console_when_no_url(self):
        self.assertIsInstance(platform_sms_gateway(), ConsoleGateway)
        self.assertTrue(ConsoleGateway().send("8801712345678", "hi").ok)

    @override_settings(SMS_GATEWAY_URL="https://sms.example/api?key={api_key}&to={to}&from={sender}&text={message}",
                       SMS_GATEWAY_METHOD="GET", SMS_GATEWAY_API_KEY="k 1", SMS_GATEWAY_SENDER="Shop", SMS_GATEWAY_SUCCESS_TEXT="202")
    def test_http_gateway(self):
        gateway = platform_sms_gateway()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"response_code":202}'
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = gateway.send("8801712345678", "Bill & total: ৳50")
        request = urlopen.call_args.args[0]
        self.assertTrue(result.ok)
        self.assertEqual(request.full_url, "https://sms.example/api?key=k%201&to=8801712345678&from=Shop&text=Bill%20%26%20total%3A%20%E0%A7%B350")
        response.__enter__.return_value.read.return_value = b'{"response_code":1003,"error":"low balance"}'
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertFalse(gateway.send("8801712345678", "x").ok)  # no success text in the answer

    def test_post_sends_a_form(self):
        gateway = HttpGateway("https://wa.example/send?token={api_key}&phone={to}&body={message}", method="POST", api_key="t")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertTrue(gateway.send("8801712345678", "hi there").ok)
        request = urlopen.call_args.args[0]
        self.assertEqual((request.full_url, request.data, request.get_method()),
                         ("https://wa.example/send", b"token=t&phone=8801712345678&body=hi%20there", "POST"))

    def test_network_failure(self):
        import urllib.error

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            result = HttpGateway("https://x.example/?to={to}").send("1", "x")
        self.assertEqual((result.ok, result.detail), (False, "timed out"))

    def test_whatsapp_link(self):
        self.assertEqual(whatsapp_link("8801712345678", "Bill: 50 TK"), "https://wa.me/8801712345678?text=Bill%3A%2050%20TK")


class PharmacyCase(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.ctx = tenant_context(self.tenant)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.outlet = Outlet.objects.get()
        self.cash = PaymentAccount.objects.get(name="Cash")
        self.napa = make_medicine(self.tenant, "Napa", conversion=10, purchase_price="5.00", sale_price="10.00")
        batch = get_or_create_batch(self.napa, "B1", TODAY + timedelta(days=400), Decimal("5"))
        post_movement(outlet=self.outlet, batch=batch, quantity=1000, kind=StockMovement.Kind.OPENING)
        self.register = start_register(self.owner, self.outlet, "0")
        self.rahim = Customer.objects.create(name="Rahim", phone="01712345678", email="rahim@x.test")

    def sell(self, qty, *, customer=None, paid=None, points=0, now=None):
        price = Decimal("10") * qty
        return complete_sale(tenant=self.tenant, outlet=self.outlet, user=self.owner, register=self.register, customer=customer,
                             lines=[{"medicine": self.napa.pk, "qty": qty}], points=points, now=now,
                             payments=[{"account": self.cash.pk, "amount": str(price if paid is None else paid)}])

    def credit_kinds(self):
        return list(SmsCredit.objects.order_by("id").values_list("kind", flat=True))


class LoyaltyTests(PharmacyCase):
    def setUp(self):
        super().setUp()
        prog = loyalty.program()
        prog.enabled, prog.earn_amount, prog.point_value, prog.min_redeem, prog.max_redeem_percent = True, Decimal("100"), Decimal("1"), 20, 50
        prog.save()

    def points(self):
        self.rahim.refresh_from_db()
        return self.rahim.points

    def test_earn_spend_and_take_back(self):
        first = self.sell(30, customer=self.rahim)  # 300 -> 3 points
        self.assertEqual((first.points_earned, self.points()), (3, 3))
        self.assertEqual(self.sell(5).points_earned, 0)  # walk-in
        loyalty.adjust(self.rahim, 97, note="Welcome gift", user=self.owner)
        self.assertEqual(self.points(), 100)

        spent = self.sell(20, customer=self.rahim, points=50, paid=150)  # a 200 bill; 50 points take 50 off
        self.assertEqual((spent.points_redeemed, spent.points_discount, spent.discount, spent.total),
                         (50, Decimal("50"), Decimal("50"), Decimal("150")))
        self.assertEqual((spent.points_earned, self.points()), (1, 51))
        self.assertEqual(list(spent.point_entries.order_by("id").values_list("kind", "change", "balance")),
                         [("redeemed", -50, 50), ("earned", 1, 51)])

        with self.assertRaisesMessage(SaleError, "At least 20"):
            self.sell(10, customer=self.rahim, points=10)
        with self.assertRaisesMessage(SaleError, "only has 51"):
            self.sell(10, customer=self.rahim, points=60)
        loyalty.adjust(self.rahim, 100, note="Promo", user=self.owner)
        with self.assertRaisesMessage(SaleError, "At most 50 points"):
            self.sell(10, customer=self.rahim, points=60, paid=40)  # a 100 bill: half is 50
        with self.assertRaisesMessage(SaleError, "Pick the customer"):
            self.sell(10, points=20)
        with self.assertRaisesMessage(SaleError, "too small"):
            self.sell(3, customer=self.rahim, points=20)  # half of 30 is under the 20-point minimum
        self.assertEqual(loyalty.most_points(self.rahim, Decimal("30"), loyalty.program()), 0)

        # Half the first bill comes back: 1 of its 3 points goes; the other half: the other 2.
        line = first.lines.get()
        create_return(first, {line.pk: 15}, date=TODAY, refund_amount="150", refund_account=self.cash)
        self.assertEqual(self.points(), 150)
        create_return(first, {line.pk: 15}, date=TODAY, refund_amount="150", refund_account=self.cash)
        self.assertEqual(self.points(), 148)
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.adjust(self.rahim, -500, note="Too much")
        self.assertTrue(books.trial_balance(TODAY)["balanced"])

    def test_switched_off(self):
        prog = loyalty.program()
        prog.enabled = False
        prog.save()
        self.assertEqual(self.sell(50, customer=self.rahim).points_earned, 0)
        Customer.objects.filter(pk=self.rahim.pk).update(points=100)
        self.rahim.refresh_from_db()
        with self.assertRaisesMessage(SaleError, "switched off"):
            self.sell(10, customer=self.rahim, points=20)


class MessageTests(PharmacyCase):
    def test_bill_after_sale_uses_credits(self):
        ms = message_settings()
        ms.invoice_sms = ms.invoice_email = True
        ms.save()
        add_credits(self.tenant, credits=3, amount=Decimal("1.05"), user=self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            sale = self.sell(12, customer=self.rahim, paid=100)
            result = after_sale(sale, base_url="https://shop.example/", user=self.owner)
        self.assertEqual(len(result["sent"]), 2)
        self.assertTrue(result["whatsapp"].startswith("https://wa.me/8801712345678?text="))
        sms = Message.objects.get(channel="sms")
        self.assertEqual((sms.status, sms.to, sms.credits, sms.kind, sms.sale), ("sent", "8801712345678", sms.parts, "invoice", sale))
        self.assertIn(sale.number, sms.body)
        self.assertIn("Tk 120, paid Tk 100, due Tk 20", sms.body)
        self.assertIn("https://shop.example/b/", sms.body)
        self.assertEqual(credit_balance(), 3 - sms.parts)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["rahim@x.test"])
        self.assertIn(sale.number, mail.outbox[0].subject)
        self.assertIn(self.tenant.name, mail.outbox[0].from_email)

        # Out of credit: refused, nothing taken.
        add_credits(self.tenant, credits=-credit_balance())
        with self.captureOnCommitCallbacks(execute=True):
            after_sale(self.sell(1, customer=self.rahim), user=self.owner)
        last = Message.objects.filter(channel="sms").first()
        self.assertEqual((last.status, last.credits), ("failed", 0))
        self.assertIn("Not enough SMS credit", last.detail)
        with self.assertRaises(MessageError):
            add_credits(self.tenant, credits=-1)

    def test_gateway_failure_gives_the_credit_back(self):
        ms = message_settings()
        ms.invoice_sms = True
        ms.save()
        add_credits(self.tenant, credits=5)
        with self.settings(SMS_GATEWAY_URL="https://sms.example/?to={to}&m={message}"), \
                mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")), \
                self.captureOnCommitCallbacks(execute=True):
            after_sale(self.sell(1, customer=self.rahim))
        message = Message.objects.get()
        self.assertEqual((message.status, message.detail), ("failed", "down"))
        self.assertEqual(credit_balance(), 5)
        self.assertEqual(self.credit_kinds(), ["purchase", "used", "refund"])

    def test_off_and_bad_numbers_fail_at_once(self):
        ms = message_settings()
        ms.sms_source, ms.invoice_sms = MessageSettings.SmsSource.OFF, True
        ms.save()
        bad = queue(channel="sms", kind="test", to="12345", body="x")
        self.assertEqual((bad.status, bad.detail), ("failed", "“12345” isn't a mobile number."))
        off = queue(channel="sms", kind="test", to="01712345678", body="x")
        self.assertIn("switched off", off.detail)
        self.assertIn("No WhatsApp gateway", queue(channel="whatsapp", kind="test", to="01712345678", body="x").detail)
        Customer.objects.filter(pk=self.rahim.pk).update(phone="")
        self.rahim.refresh_from_db()
        after_sale(self.sell(1, customer=self.rahim))  # no number: nothing is tried
        self.assertEqual(Message.objects.count(), 3)

    def test_public_bill_link(self):
        sale = self.sell(3, customer=self.rahim)
        url = bill_url(sale, "http://testserver")
        token = url.rstrip("/").rsplit("/", 1)[1]
        self.assertEqual(read_bill_token(token), (self.tenant.pk, sale.pk))
        page = self.client.get(url)  # nobody logged in
        self.assertContains(page, sale.number)
        self.assertContains(page, "Rahim")
        self.assertIsNone(read_bill_token(token[:-1] + ("A" if token[-1] != "A" else "B")))
        self.assertEqual(self.client.get(url.replace(f"-{sale.pk}-", f"-{sale.pk + 1}-")).status_code, 404)

    def test_due_reminders(self):
        self.sell(30, customer=self.rahim, paid=100)  # owes 200
        karim = Customer.objects.create(name="Karim", phone="01811111111")
        complete_sale(tenant=self.tenant, outlet=self.outlet, user=self.owner, register=self.register, customer=karim,
                      lines=[{"medicine": self.napa.pk, "qty": 5}], payments=[])  # owes 50, under the 100 minimum
        self.assertEqual([c.name for c in due_customers()], ["Rahim"])
        with self.captureOnCommitCallbacks(execute=True):
            send_due_reminders(list(due_customers()), user=self.owner)
        self.assertEqual(Message.objects.get().status, "failed")  # no MediPOS credit yet
        self.assertEqual([c.name for c in due_customers()], ["Rahim"])  # a failed one doesn't count
        add_credits(self.tenant, credits=10)
        with self.captureOnCommitCallbacks(execute=True):
            sent = send_due_reminders(list(due_customers()), user=self.owner)
        sent[0].refresh_from_db()
        self.assertEqual(sent[0].status, "sent")
        self.assertIn("Tk 200", sent[0].body)
        self.assertEqual(list(due_customers()), [])  # reminded already
        self.assertEqual(len(due_customers(fresh_only=False)), 1)

    def test_daily_alert_and_reminders_once_a_day(self):
        low = make_medicine(self.tenant, "Seclo", alert_quantity=100)
        batch = get_or_create_batch(low, "S1", TODAY + timedelta(days=10), Decimal("5"))
        post_movement(outlet=self.outlet, batch=batch, quantity=20, kind=StockMovement.Kind.OPENING)  # low, and expiring in 10 days
        ms = message_settings()
        ms.daily_alerts, ms.alert_phones, ms.alert_emails, ms.alert_hour = True, "01811111111", "boss@x.test", 9
        ms.save()
        add_credits(self.tenant, credits=10)
        morning = timezone.make_aware(datetime.combine(TODAY, datetime.min.time()).replace(hour=8))
        self.assertIsNone(run_daily(morning))  # before the hour
        with self.captureOnCommitCallbacks(execute=True):
            done = run_daily(morning + timedelta(hours=2), base_url="https://shop.example")
        self.assertEqual(len(done["alerts"]), 2)
        sms = Message.objects.get(channel="sms")
        self.assertIn("1 low, 0 out of stock, 1 batches expire within 30 days, 0 expired", sms.body)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Seclo", mail.outbox[0].alternatives[0][0])
        self.assertIsNone(run_daily(morning + timedelta(hours=3)))  # once a day
        self.assertIsNotNone(run_daily(morning + timedelta(hours=3), force=True))

    def test_campaign(self):
        lapsed = Customer.objects.create(name="Anwar", phone="01911111111")
        Customer.objects.create(name="No offers", phone="01611111111", no_marketing=True)
        Customer.objects.create(name="No phone")
        self.sell(5, customer=self.rahim)
        self.sell(5, customer=lapsed, now=timezone.now() - timedelta(days=90))
        campaign = Campaign.objects.create(name="Winter offer", text="Dear {name}, 10% off at {shop} this week.")
        self.assertEqual(preview(campaign)["count"], 2)
        campaign.audience, campaign.days = Campaign.Audience.LAPSED, 60
        campaign.save()
        self.assertEqual([c.name for c in preview(campaign)["people"]], ["Anwar"])
        campaign.audience = Campaign.Audience.RECENT
        campaign.save()
        self.assertEqual([c.name for c in preview(campaign)["people"]], ["Rahim"])
        campaign.audience = Campaign.Audience.ALL
        campaign.save()
        with self.assertRaisesMessage(MessageError, "needs 2 SMS credits and 0 are left"):
            send_campaign(campaign)
        add_credits(self.tenant, credits=2)
        with self.captureOnCommitCallbacks(execute=True):
            sent = send_campaign(campaign, user=self.owner)
        self.assertEqual(sorted(m.customer.name for m in sent), ["Anwar", "Rahim"])
        self.assertTrue(Message.objects.filter(campaign=campaign, status="sent", body__startswith="Dear Anwar").exists())
        with self.assertRaisesMessage(MessageError, "already sent"):
            send_campaign(campaign)


class PageTests(PharmacyCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def post_json(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_pages(self):
        sale = self.sell(3, customer=self.rahim)
        for name in ("messaging:outbox", "messaging:settings", "messaging:due_reminders", "messaging:campaigns", "messaging:campaign_create",
                     "messaging:points", "messaging:loyalty_settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("messaging:outbox_data"), {"draw": 1}).json()["recordsTotal"], 0)
        self.assertContains(self.client.get(reverse("sales:detail", args=[sale.pk])), "Send the bill")
        self.assertEqual(self.client.get(reverse("customers:detail", args=[self.rahim.pk])).status_code, 200)

        # Settings: a template word that doesn't exist is caught.
        settings_url = reverse("messaging:settings")
        data = {"sms_source": "platform", "sms_method": "GET", "whatsapp_method": "POST", "invoice_sms": "on", "invoice_text": "Hi {nmae}",
                "due_every_days": 7, "due_min_amount": "100", "due_text": "{name} owes {due}", "alert_hour": 9, "alert_expiry_days": 30}
        self.assertContains(self.client.post(settings_url, data), "Unknown: {nmae}")
        data["invoice_text"] = "Hi {name}, bill {invoice} {total}"
        self.assertRedirects(self.client.post(settings_url, data), settings_url)
        self.assertTrue(message_settings().invoice_sms)

        # A campaign: create, preview, send (credit first), and see it in the outbox.
        response = self.client.post(reverse("messaging:campaign_create"), {"name": "Eid", "channel": "sms", "audience": "all", "days": 30,
                                                                            "min_points": 100, "text": "Eid Mubarak {name}!"})
        campaign = Campaign.objects.get()
        self.assertRedirects(response, reverse("messaging:campaign", args=[campaign.pk]))
        self.assertContains(self.client.get(reverse("messaging:campaign", args=[campaign.pk])), "Eid Mubarak Rahim!")
        add_credits(self.tenant, credits=5)
        self.client.post(reverse("messaging:campaign", args=[campaign.pk]), {"action": "send"})
        self.assertEqual(Message.objects.filter(campaign=campaign).count(), 1)
        self.assertEqual(self.client.get(reverse("messaging:outbox_data"), {"draw": 1}).json()["recordsTotal"], 1)

        # Send a bill from the sale page, a test, and correct points.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("messaging:sale_send", args=[sale.pk]), {"channel": "email", "to": "someone@x.test"})
        self.assertEqual(mail.outbox[-1].to, ["someone@x.test"])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("messaging:outbox"), {"action": "test", "test-channel": "sms", "test-to": "01712345678"})
        self.assertEqual(Message.objects.get(kind="test").status, "sent")
        self.client.post(reverse("messaging:adjust_points", args=[self.rahim.pk]), {"change": 25, "note": "Gift"})
        self.rahim.refresh_from_db()
        self.assertEqual(self.rahim.points, 25)

        # A due reminder from the customer page goes where it was typed.
        self.sell(20, customer=self.rahim, paid=50)
        due_page = self.client.get(reverse("messaging:due_reminders"))
        self.assertContains(due_page, "Rahim")
        self.assertContains(due_page, "https://wa.me/8801712345678")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("messaging:remind_customer", args=[self.rahim.pk]), {"channel": "email", "to": "son@x.test"})
        self.assertEqual(mail.outbox[-1].to, ["son@x.test"])
        self.assertIn("Tk 150", mail.outbox[-1].body)

    def test_pos_checkout_spends_points_and_offers_whatsapp(self):
        prog = loyalty.program()
        prog.enabled, prog.min_redeem = True, 10
        prog.save()
        Customer.objects.filter(pk=self.rahim.pk).update(points=40)
        self.client.post(reverse("sales:register_open"), {"opening_cash": "0"})
        lookup = self.client.get(reverse("customers:lookup"), {"q": "Rahim"}).json()["results"][0]
        self.assertEqual(lookup["points"], 40)
        data = self.post_json("sales:pos_checkout", {"lines": [{"medicine": self.napa.pk, "qty": 10}], "customer": self.rahim.pk, "points": 30,
                                                     "payments": [{"account": self.cash.pk, "amount": 70}]}).json()
        self.assertEqual(data["points"], {"earned": 0, "balance": 10})
        self.assertTrue(data["whatsappUrl"].startswith("https://wa.me/8801712345678"))
        receipt = self.client.get(data["receiptUrl"])
        self.assertContains(receipt, "Points spent (30)")

    def test_cashier_without_message_rights(self):
        cashier = add_staff(self.tenant, "c@x.test", ["pos.create", "customer.list"], all_outlets=True)
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("messaging:settings")).status_code, 403)
        self.assertEqual(self.client.get(reverse("messaging:outbox")).status_code, 403)


class PlatformCreditTests(TestCase):
    def test_platform_adds_credits(self):
        from apps.accounts.models import User

        tenant, _owner = make_pharmacy()
        admin = User.objects.create_superuser("root@x.test", "pw")
        self.client.force_login(admin)
        url = reverse("platform:tenant_action", args=[tenant.pk, "sms"])
        self.client.post(url, {"credits": 500, "amount": "175", "note": "bKash TX1"})
        with tenant_context(tenant):
            self.assertEqual(credit_balance(), 500)
        self.assertContains(self.client.get(reverse("platform:tenant_detail", args=[tenant.pk])), "bKash TX1")
        self.client.post(url, {"credits": -600})
        with tenant_context(tenant):
            self.assertEqual(credit_balance(), 500)
