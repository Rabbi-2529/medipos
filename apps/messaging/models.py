from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

DEFAULT_INVOICE_TEXT = "{shop}: Thank you {name}. Bill {invoice}: {total}, paid {paid}, due {due}. Points: {points_balance}. {link}"
DEFAULT_DUE_TEXT = "Dear {name}, your due at {shop} is {due}. Please clear it on your next visit. {shop_phone}"


class Channel(models.TextChoices):
    SMS = "sms", _("SMS")
    WHATSAPP = "whatsapp", _("WhatsApp")
    EMAIL = "email", _("Email")


class Method(models.TextChoices):
    GET = "GET", "GET"
    POST = "POST", "POST"


class MessageSettings(TenantOwnedModel):
    """How a pharmacy sends messages, and which ones go out by themselves. One per pharmacy."""

    class SmsSource(models.TextChoices):
        PLATFORM = "platform", _("MediPOS SMS credits")
        OWN = "own", _("Our own SMS gateway")
        OFF = "off", _("No SMS")

    sms_source = models.CharField(_("send SMS through"), max_length=10, choices=SmsSource.choices, default=SmsSource.PLATFORM)
    sms_url = models.CharField(_("gateway URL"), max_length=500, blank=True,
                               help_text=_("From your SMS provider, with {to}, {message}, {sender} and {api_key} where they go."))
    sms_method = models.CharField(_("method"), max_length=4, choices=Method.choices, default=Method.GET)
    sms_api_key = models.CharField(_("API key"), max_length=200, blank=True)
    sms_sender = models.CharField(_("sender ID"), max_length=30, blank=True, help_text=_("The name or number customers see."))
    sms_success_text = models.CharField(_("success text"), max_length=60, blank=True,
                                        help_text=_("Text the provider's answer contains when the SMS went out, e.g. 202. Empty = any answer."))
    whatsapp_url = models.CharField(_("WhatsApp gateway URL"), max_length=500, blank=True,
                                    help_text=_("Optional. Without it, WhatsApp buttons open the chat with the message typed in."))
    whatsapp_method = models.CharField(_("method"), max_length=4, choices=Method.choices, default=Method.POST)
    whatsapp_api_key = models.CharField(_("API key"), max_length=200, blank=True)
    whatsapp_success_text = models.CharField(_("success text"), max_length=60, blank=True)
    email_from_name = models.CharField(_("email from name"), max_length=80, blank=True, help_text=_("Empty = the shop name."))
    email_reply_to = models.EmailField(_("replies go to"), blank=True)

    # What goes out by itself
    invoice_sms = models.BooleanField(_("SMS the bill after a sale"), default=False)
    invoice_whatsapp = models.BooleanField(_("WhatsApp the bill after a sale"), default=False)
    invoice_email = models.BooleanField(_("Email the bill after a sale"), default=False)
    invoice_text = models.TextField(_("bill message"), default=DEFAULT_INVOICE_TEXT)
    due_reminders = models.BooleanField(_("Remind customers of their due"), default=False)
    due_every_days = models.PositiveSmallIntegerField(_("at most every (days)"), default=7)
    due_min_amount = models.DecimalField(_("when they owe at least"), max_digits=12, decimal_places=2, default=Decimal("100"))
    due_text = models.TextField(_("due reminder message"), default=DEFAULT_DUE_TEXT)
    daily_alerts = models.BooleanField(_("Daily low-stock and expiry alert"), default=False)
    alert_hour = models.PositiveSmallIntegerField(_("send at (hour)"), default=9)
    alert_expiry_days = models.PositiveSmallIntegerField(_("count batches expiring within (days)"), default=30)
    alert_phones = models.CharField(_("alert mobiles"), max_length=255, blank=True, help_text=_("Separate several with commas."))
    alert_emails = models.CharField(_("alert emails"), max_length=255, blank=True, help_text=_("Separate several with commas."))
    last_daily_run = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="one_message_settings")]

    def __str__(self):
        return f"Message settings ({self.tenant_id})"


class SmsCredit(TenantOwnedModel):
    """The pharmacy's MediPOS SMS credit: bought from the platform, used one per SMS part."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase", _("Bought")
        USED = "used", _("Used")
        REFUND = "refund", _("Refunded")
        ADJUST = "adjust", _("Adjusted")

    kind = models.CharField(max_length=10, choices=Kind.choices)
    change = models.IntegerField()
    amount = models.DecimalField(_("amount paid"), max_digits=12, decimal_places=2, default=Decimal("0"))
    note = models.CharField(_("note"), max_length=255, blank=True)
    message = models.ForeignKey("Message", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]


class Campaign(TenantOwnedModel):
    """One message to many customers at once."""

    class Audience(models.TextChoices):
        ALL = "all", _("Every customer")
        DUE = "due", _("Customers who owe money")
        RECENT = "recent", _("Bought in the last N days")
        LAPSED = "lapsed", _("Haven't bought for N days")
        POINTS = "points", _("Have at least N points")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")

    name = models.CharField(_("name"), max_length=120)
    channel = models.CharField(_("send by"), max_length=10, choices=Channel.choices, default=Channel.SMS)
    audience = models.CharField(_("to"), max_length=10, choices=Audience.choices, default=Audience.ALL)
    days = models.PositiveSmallIntegerField(_("days"), default=30)
    min_points = models.PositiveIntegerField(_("points"), default=100)
    subject = models.CharField(_("email subject"), max_length=150, blank=True)
    text = models.TextField(_("message"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT


class Message(TenantOwnedModel):
    """Every SMS, WhatsApp and email the shop sent or tried to send."""

    class Kind(models.TextChoices):
        INVOICE = "invoice", _("Bill")
        DUE = "due", _("Due reminder")
        ALERT = "alert", _("Stock alert")
        CAMPAIGN = "campaign", _("Campaign")
        TEST = "test", _("Test")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Waiting")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    channel = models.CharField(max_length=10, choices=Channel.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    to = models.CharField(max_length=150)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    html = models.TextField(blank=True)  # emails only
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    detail = models.CharField(max_length=300, blank=True)  # the gateway's answer or the reason it failed
    parts = models.PositiveSmallIntegerField(default=0)  # SMS parts
    credits = models.PositiveSmallIntegerField(default=0)  # MediPOS credits taken (refunded if it failed)
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    sale = models.ForeignKey("sales.Sale", on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    campaign = models.ForeignKey(Campaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status"]), models.Index(fields=["tenant", "customer", "kind"])]

    def __str__(self):
        return f"{self.channel} to {self.to}"


class LoyaltyProgram(TenantOwnedModel):
    """Customers earn points on what they buy and spend them as money off a later bill. One per pharmacy."""

    enabled = models.BooleanField(_("Loyalty points are on"), default=False)
    earn_amount = models.DecimalField(_("spend for 1 point"), max_digits=10, decimal_places=2, default=Decimal("100"),
                                      help_text=_("e.g. 100 = one point for every 100 spent."))
    point_value = models.DecimalField(_("1 point is worth"), max_digits=8, decimal_places=2, default=Decimal("1"))
    min_redeem = models.PositiveIntegerField(_("points needed to spend them"), default=50)
    max_redeem_percent = models.PositiveSmallIntegerField(_("pay at most this % of a bill with points"), default=50)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="one_loyalty_program")]


class PointEntry(TenantOwnedModel):
    """Every change to a customer's points. Customer.points is the running total."""

    class Kind(models.TextChoices):
        EARNED = "earned", _("Earned")
        REDEEMED = "redeemed", _("Spent")
        RETURNED = "returned", _("Taken back (return)")
        ADJUSTED = "adjusted", _("Adjusted")

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="point_entries")
    date = models.DateField(_("date"))
    kind = models.CharField(max_length=10, choices=Kind.choices)
    change = models.IntegerField()
    balance = models.IntegerField()  # the customer's points after this entry
    sale = models.ForeignKey("sales.Sale", on_delete=models.SET_NULL, null=True, blank=True, related_name="point_entries")
    note = models.CharField(_("note"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
