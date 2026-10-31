from django.test import TestCase
from django.urls import reverse

from apps.core.tenancy import tenant_context
from apps.core.tests.factories import make_pharmacy

from .models import PaymentAccount


class PaymentAccountTests(TestCase):
    def setUp(self):
        self.tenant, self.owner = make_pharmacy()
        self.client.force_login(self.owner)

    def _create(self, **data):
        payload = {"name": "DBBL", "kind": "bank", "provider": "Dutch-Bangla Bank", "account_number": "123-456",
                   "opening_balance": "5000", "sort_order": "5"}
        payload.update(data)
        return self.client.post(reverse("accounting:payment_account_create"), payload)

    def test_list_and_create(self):
        response = self.client.get(reverse("accounting:payment_accounts"))
        self.assertContains(response, "bKash")
        self.assertContains(response, "৳0.00")
        self.assertRedirects(self._create(), reverse("accounting:payment_accounts"))
        with tenant_context(self.tenant):
            self.assertEqual(PaymentAccount.objects.get(name="DBBL").opening_balance, 5000)

    def test_duplicate_name_is_a_form_error(self):
        self.assertContains(self._create(name="Cash"), "already exists")

    def test_keeps_one_active_account(self):
        with tenant_context(self.tenant):
            accounts = list(PaymentAccount.objects.all())
        for account in accounts[:-1]:
            self.client.post(reverse("accounting:payment_account_toggle", args=[account.pk]))
        response = self.client.post(reverse("accounting:payment_account_toggle", args=[accounts[-1].pk]), follow=True)
        self.assertContains(response, "at least one active payment account")
