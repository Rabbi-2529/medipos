from apps.core.forms import TenantModelForm

from .models import PaymentAccount


class PaymentAccountForm(TenantModelForm):
    class Meta:
        model = PaymentAccount
        fields = ["name", "kind", "provider", "account_number", "opening_balance", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
