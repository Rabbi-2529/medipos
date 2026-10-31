from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from apps.core.shop_views import ShopCreateView, ShopListView, ShopToggleView, ShopUpdateView

from .forms import PaymentAccountForm
from .models import PaymentAccount

ACCOUNTS = reverse_lazy("accounting:payment_accounts")


class PaymentAccountListView(ShopListView):
    permission = "payment_account.list"
    template_name = "accounting/payment_account_list.html"
    title = _("Payment accounts")

    def get_queryset(self):
        return PaymentAccount.objects.all()


class PaymentAccountCreateView(ShopCreateView):
    permission = "payment_account.create"
    form_class = PaymentAccountForm
    title = _("Add payment account")
    success_url = ACCOUNTS


class PaymentAccountUpdateView(ShopUpdateView):
    permission = "payment_account.edit"
    form_class = PaymentAccountForm
    title = _("Edit payment account")
    success_url = ACCOUNTS

    def get_queryset(self):
        return PaymentAccount.objects.all()


class PaymentAccountToggleView(ShopToggleView):
    permission = "payment_account.delete"
    model = PaymentAccount
    success_url = ACCOUNTS

    def can_deactivate(self, account):
        if not PaymentAccount.objects.filter(is_active=True).exclude(pk=account.pk).exists():
            return _("Keep at least one active payment account, or sales can't be paid.")
        return None
