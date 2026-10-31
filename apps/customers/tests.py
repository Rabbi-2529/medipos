import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import activate

from apps.accounting.models import PaymentAccount
from apps.core.tenancy import tenant_context
from apps.core.tests.factories import make_pharmacy
from apps.setup.services import get_business_settings
from apps.setup.templatetags.shop import bn_digits

from .models import Customer
from .services import CustomerError, balance, collect, set_opening_balance


class CustomerTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def test_opening_due_and_collection(self):
        with tenant_context(self.tenant):
            rahim = Customer.objects.create(name="Rahim", phone="01711", opening_balance=Decimal("300"))
            set_opening_balance(rahim)
            cash = PaymentAccount.objects.get(name="Cash")
            receipt = collect(rahim, amount=Decimal("100"), account=cash, date=timezone.localdate())
            self.assertEqual((receipt.number, balance(rahim)), ("RCV-00001", Decimal("200")))
            with self.assertRaises(CustomerError):
                collect(rahim, amount=Decimal("0"), account=cash, date=timezone.localdate())

    def test_quick_add_and_lookup(self):
        response = self.client.post(reverse("customers:quick_create"), json.dumps({"name": "Karim", "phone": "01822", "credit_limit": "500"}),
                                    content_type="application/json")
        self.assertEqual(response.json()["credit_limit"], "500.00")
        duplicate = self.client.post(reverse("customers:quick_create"), json.dumps({"name": "K2", "phone": "01822"}), content_type="application/json")
        self.assertEqual(duplicate.status_code, 400)
        other, _ = make_pharmacy("Green Life")
        with tenant_context(other):
            Customer.objects.create(name="Karim Other", phone="01822")
        results = self.client.get(reverse("customers:lookup"), {"q": "karim"}).json()["results"]
        self.assertEqual([r["name"] for r in results], ["Karim"])

    def test_list_and_detail(self):
        with tenant_context(self.tenant):
            rahim = Customer.objects.create(name="Rahim", phone="017", opening_balance=Decimal("50"))
            set_opening_balance(rahim)
        data = self.client.get(reverse("customers:data"), {"draw": 1, "start": 0, "length": 10, "due": "yes", "status": "active"}).json()
        self.assertEqual(data["recordsTotal"], 1)
        self.assertContains(self.client.get(reverse("customers:detail", args=[rahim.pk])), "Opening due")


class LanguageTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()

    def test_switching_saves_on_the_account(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("accounts:language"), {"language": "bn", "next": "/"})
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.language, "bn")
        self.assertContains(self.client.get(reverse("core:dashboard")), "ড্যাশবোর্ড")
        self.assertEqual(self.client.post(reverse("accounts:language"), {"language": "xx"}).status_code, 400)

    def test_pharmacy_default_applies_until_user_picks(self):
        settings = get_business_settings(self.tenant)
        settings.default_language = "bn"
        settings.save()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("core:dashboard")), "ড্যাশবোর্ড")
        self.owner.language = "en"
        self.owner.save()
        self.assertContains(self.client.get(reverse("core:dashboard")), "Dashboard")

    def test_login_page_switch_uses_cookie(self):
        response = self.client.post(reverse("accounts:language"), {"language": "bn", "next": reverse("accounts:login")})
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertContains(self.client.get(reverse("accounts:login")), "লগইন")

    def test_unsafe_next_is_ignored(self):
        response = self.client.post(reverse("accounts:language"), {"language": "bn", "next": "https://evil.example/"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    @override_settings(LANGUAGE_CODE="en")
    def test_bangla_digits_filter(self):
        activate("bn")
        self.assertEqual(bn_digits("৳1,250.50"), "৳১,২৫০.৫০")
        self.assertEqual(bn_digits("123", False), "123")
        activate("en")
        self.assertEqual(bn_digits("123"), "123")
