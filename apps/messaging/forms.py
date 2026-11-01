from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin, TenantModelForm

from .models import Campaign, Channel, LoyaltyProgram, MessageSettings
from .text import normalize_phone, placeholders

BILL_WORDS = ("name", "shop", "shop_phone", "invoice", "date", "total", "paid", "due", "points", "points_balance", "link")
CUSTOMER_WORDS = ("name", "shop", "shop_phone", "due", "points_balance")


def _check_words(text, allowed):
    unknown = sorted({w for w in placeholders(text) if w not in allowed})
    if unknown:
        raise ValidationError(_("Unknown: %(words)s. You can use %(allowed)s.") % {
            "words": ", ".join("{" + w + "}" for w in unknown), "allowed": " ".join("{" + w + "}" for w in allowed)})
    return text


def _check_url(url):
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        raise ValidationError(_("Start with https:// (or http://)."))
    if url and "{to}" not in url:
        raise ValidationError(_("Put {to} where the mobile number goes."))
    if url and "{message}" not in url:
        raise ValidationError(_("Put {message} where the text goes."))
    return url


class MessageSettingsForm(TenantModelForm):
    class Meta:
        model = MessageSettings
        fields = [
            "sms_source", "sms_url", "sms_method", "sms_api_key", "sms_sender", "sms_success_text",
            "whatsapp_url", "whatsapp_method", "whatsapp_api_key", "whatsapp_success_text", "email_from_name", "email_reply_to",
            "invoice_sms", "invoice_whatsapp", "invoice_email", "invoice_text",
            "due_reminders", "due_every_days", "due_min_amount", "due_text",
            "daily_alerts", "alert_hour", "alert_expiry_days", "alert_phones", "alert_emails",
        ]
        widgets = {"invoice_text": forms.Textarea(attrs={"rows": 3}), "due_text": forms.Textarea(attrs={"rows": 3}),
                   "sms_api_key": forms.PasswordInput(render_value=True), "whatsapp_api_key": forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["alert_hour"].widget = forms.Select(choices=[(h, f"{h:02d}:00") for h in range(24)], attrs={"class": "form-select"})
        for name in ("sms_source", "sms_method", "whatsapp_method"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        for name in ("invoice_text", "due_text"):
            self.fields[name].widget.attrs["data-sms-count"] = ""

    def clean_sms_url(self):
        return _check_url(self.cleaned_data["sms_url"])

    def clean_whatsapp_url(self):
        return _check_url(self.cleaned_data["whatsapp_url"])

    def clean_invoice_text(self):
        return _check_words(self.cleaned_data["invoice_text"], BILL_WORDS)

    def clean_due_text(self):
        return _check_words(self.cleaned_data["due_text"], CUSTOMER_WORDS)

    def clean_alert_hour(self):
        hour = self.cleaned_data["alert_hour"]
        if not 0 <= hour <= 23:
            raise ValidationError(_("Pick an hour of the day."))
        return hour

    def clean_alert_phones(self):
        phones = [p.strip() for p in self.cleaned_data["alert_phones"].replace(";", ",").split(",") if p.strip()]
        bad = [p for p in phones if not normalize_phone(p)]
        if bad:
            raise ValidationError(_("Not a mobile number: %(value)s") % {"value": ", ".join(bad)})
        return ", ".join(phones)

    def clean_alert_emails(self):
        emails = [e.strip() for e in self.cleaned_data["alert_emails"].replace(";", ",").split(",") if e.strip()]
        field = forms.EmailField()
        for email in emails:
            field.clean(email)
        return ", ".join(emails)

    def clean(self):
        data = super().clean()
        if data.get("sms_source") == MessageSettings.SmsSource.OWN and not data.get("sms_url"):
            self.add_error("sms_url", _("Your own gateway needs its URL."))
        if data.get("invoice_whatsapp") and not data.get("whatsapp_url"):
            self.add_error("invoice_whatsapp", _("Sending WhatsApp by itself needs a WhatsApp gateway URL."))
        if data.get("daily_alerts") and not (data.get("alert_phones") or data.get("alert_emails")):
            self.add_error("alert_phones", _("Add a mobile or an email for the daily alert."))
        return data


class TestMessageForm(BootstrapFormMixin, forms.Form):
    channel = forms.ChoiceField(label=_("Send by"), choices=Channel.choices)
    to = forms.CharField(label=_("To"), max_length=150, help_text=_("A mobile number, or an email address for email."))


class CampaignForm(TenantModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "channel", "audience", "days", "min_points", "subject", "text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 4, "data-sms-count": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("channel", "audience"):
            self.fields[name].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})
        self.fields["text"].help_text = _("You can use %(words)s.") % {"words": " ".join("{" + w + "}" for w in CUSTOMER_WORDS)}

    def clean_text(self):
        return _check_words(self.cleaned_data["text"], CUSTOMER_WORDS)

    def clean(self):
        data = super().clean()
        if data.get("channel") == Channel.EMAIL and not data.get("subject"):
            self.add_error("subject", _("An email needs a subject."))
        if data.get("audience") in (Campaign.Audience.RECENT, Campaign.Audience.LAPSED) and not data.get("days"):
            self.add_error("days", _("How many days?"))
        return data


class LoyaltyForm(TenantModelForm):
    class Meta:
        model = LoyaltyProgram
        fields = ["enabled", "earn_amount", "point_value", "min_redeem", "max_redeem_percent"]

    def clean_earn_amount(self):
        value = self.cleaned_data["earn_amount"]
        if value <= 0:
            raise ValidationError(_("Must be more than zero."))
        return value

    def clean_point_value(self):
        value = self.cleaned_data["point_value"]
        if value <= 0:
            raise ValidationError(_("Must be more than zero."))
        return value

    def clean_max_redeem_percent(self):
        value = self.cleaned_data["max_redeem_percent"]
        if not 1 <= value <= 100:
            raise ValidationError(_("Between 1 and 100."))
        return value


class AdjustPointsForm(BootstrapFormMixin, forms.Form):
    change = forms.IntegerField(label=_("Points"), help_text=_("A minus number takes points away."))
    note = forms.CharField(label=_("Why"), max_length=255)


class SendForm(BootstrapFormMixin, forms.Form):
    """Send a bill or a due reminder to one person by SMS or email (WhatsApp has its own button)."""

    channel = forms.ChoiceField(label=_("Send by"), choices=[(Channel.SMS, Channel.SMS.label), (Channel.EMAIL, Channel.EMAIL.label),
                                                              (Channel.WHATSAPP, _("WhatsApp gateway"))])
    to = forms.CharField(label=_("To"), max_length=150)


class CreditsForm(BootstrapFormMixin, forms.Form):
    """Platform console: SMS credit sold to a pharmacy."""

    credits = forms.IntegerField(label=_("SMS credits"), help_text=_("One credit = one SMS part. A minus number takes credits away."))
    amount = forms.DecimalField(label=_("Amount paid (৳)"), max_digits=12, decimal_places=2, min_value=0, required=False)
    note = forms.CharField(label=_("Note"), max_length=255, required=False)
