from django.utils.translation import gettext_lazy as _

from apps.core.forms import TenantModelForm

from .models import Counter, Outlet


class OutletForm(TenantModelForm):
    class Meta:
        model = Outlet
        fields = ["name", "code", "phone", "email", "address"]
        help_texts = {"code": _("Short unique code, printed on invoices.")}


class CounterForm(TenantModelForm):
    class Meta:
        model = Counter
        fields = ["outlet", "name", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["outlet"].queryset = Outlet.objects.filter(is_active=True)
        self.fields["outlet"].empty_label = None
        self.fields["outlet"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
