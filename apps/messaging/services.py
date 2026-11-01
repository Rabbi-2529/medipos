"""The outbox: SMS, WhatsApp and email for bills, due reminders, the daily stock alert and campaigns, and MediPOS SMS credits."""
import base64
import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Exists, F, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.tenancy import tenant_context
from apps.customers.models import Customer
from apps.customers.services import with_balances
from apps.setup.services import get_business_settings

from . import gateways
from .models import Campaign, Channel, Message, MessageSettings, SmsCredit
from .text import fill, normalize_phone, sms_parts

ZERO = Decimal("0")
S = Message.Status


class MessageError(Exception):
    """A user-facing reason messages can't go out."""


def message_settings():
    """This pharmacy's settings (made with everything off the first time they are asked for)."""
    found = MessageSettings.objects.first()
    return found if found is not None else MessageSettings.objects.create()


# ---- MediPOS SMS credits ------------------------------------------------------------------

def credit_balance():
    return SmsCredit.objects.aggregate(t=Sum("change"))["t"] or 0


@transaction.atomic
def add_credits(tenant, *, credits, amount=ZERO, note="", user=None):
    """The platform sells (or corrects) a pharmacy's SMS credit."""
    if not credits:
        raise MessageError(_("Enter how many SMS credits to add."))
    with tenant_context(tenant):
        if credits < 0 and credit_balance() + credits < 0:
            raise MessageError(_("The pharmacy only has %(n)s SMS credits.") % {"n": credit_balance()})
        kind = SmsCredit.Kind.PURCHASE if credits > 0 else SmsCredit.Kind.ADJUST
        return SmsCredit.objects.create(kind=kind, change=credits, amount=amount or ZERO, note=note[:255], created_by=user)


# ---- Putting messages in the outbox and sending them --------------------------------------------

def channel_ready(channel, ms=None):
    """(True, "") when messages on `channel` can go out by themselves, else (False, why not)."""
    ms = ms or message_settings()
    if channel == Channel.SMS:
        if ms.sms_source == MessageSettings.SmsSource.OFF:
            return False, _("SMS is switched off in message settings.")
        if ms.sms_source == MessageSettings.SmsSource.OWN and not ms.sms_url:
            return False, _("Add your SMS gateway URL in message settings.")
    if channel == Channel.WHATSAPP and not ms.whatsapp_url:
        return False, _("No WhatsApp gateway is set up. Use the WhatsApp button to send from your phone instead.")
    return True, ""


def queue(*, channel, kind, to, body, subject="", html="", customer=None, sale=None, campaign=None, user=None, soon=True, ms=None):
    """Put a message in the outbox. A bad number or a channel that isn't set up fails it straight away (it still shows)."""
    ms = ms or message_settings()
    message = Message(channel=channel, kind=kind, subject=subject[:200], body=body, html=html, customer=customer, sale=sale,
                      campaign=campaign, created_by=user)
    if channel == Channel.EMAIL:
        message.to = (to or "").strip()[:150]
        problem = "" if "@" in message.to else _("No email address.")
    else:
        message.to = normalize_phone(to)
        problem = "" if message.to else _("“%(value)s” isn't a mobile number.") % {"value": to or "—"}
        message.parts = sms_parts(body) if channel == Channel.SMS else 0
    if not problem:
        problem = channel_ready(channel, ms)[1]
    if problem:
        message.status, message.detail, message.to = S.FAILED, problem[:300], message.to or (to or "")[:150]
    message.save()
    if soon:
        send_soon([message])
    return message


def send_soon(messages):
    """With MESSAGES_SEND_NOW, deliver right after the page's changes are saved (a few at a time); the rest wait for send_messages."""
    if not django_settings.MESSAGES_SEND_NOW:
        return
    ids = [m.pk for m in messages if m.status == S.QUEUED][:django_settings.MESSAGES_SEND_NOW_LIMIT]
    if ids:
        transaction.on_commit(lambda: [deliver_id(pk) for pk in ids])


def deliver_id(pk):
    message = Message.all_objects.select_related("tenant").filter(pk=pk, status=S.QUEUED).first()
    return deliver(message) if message else None


def _sms(message, ms):
    Source = MessageSettings.SmsSource
    if ms.sms_source == Source.OFF:
        return gateways.Result(False, _("SMS is switched off in message settings."))
    if ms.sms_source == Source.OWN:
        if not ms.sms_url:
            return gateways.Result(False, _("Add your SMS gateway URL in message settings."))
        return gateways.HttpGateway(ms.sms_url, method=ms.sms_method, api_key=ms.sms_api_key, sender=ms.sms_sender,
                                    success_text=ms.sms_success_text).send(message.to, message.body)
    parts = message.parts or sms_parts(message.body)
    with transaction.atomic():
        MessageSettings.objects.select_for_update().get(pk=ms.pk)  # one SMS at a time takes this pharmacy's credit
        left = credit_balance()
        if left < parts:
            return gateways.Result(False, _("Not enough SMS credit: %(need)s needed, %(left)s left. Buy more from MediPOS.") % {
                "need": parts, "left": left})
        SmsCredit.objects.create(kind=SmsCredit.Kind.USED, change=-parts, message=message)
        message.credits = parts
        message.save(update_fields=["credits"])
    return gateways.platform_sms_gateway().send(message.to, message.body)


def deliver(message):
    """Send one waiting message now. Never raises: how it went is written on the message."""
    with tenant_context(message.tenant):
        ms = message_settings()
        tenant = message.tenant
        if message.channel == Channel.SMS:
            result = _sms(message, ms)
        elif message.channel == Channel.WHATSAPP:
            if ms.whatsapp_url:
                result = gateways.HttpGateway(ms.whatsapp_url, method=ms.whatsapp_method, api_key=ms.whatsapp_api_key,
                                              success_text=ms.whatsapp_success_text).send(message.to, message.body)
            else:
                result = gateways.Result(False, _("No WhatsApp gateway is set up."))
        else:
            result = gateways.send_email(to=message.to, subject=message.subject, text=message.body, html=message.html,
                                         from_name=ms.email_from_name or tenant.name, reply_to=ms.email_reply_to or tenant.email or "")
        message.status = S.SENT if result.ok else S.FAILED
        message.detail = (result.detail or "")[:300]
        message.sent_at = timezone.now() if result.ok else None
        message.save(update_fields=["status", "detail", "sent_at", "updated_at"])
        if not result.ok and message.credits:
            SmsCredit.objects.create(kind=SmsCredit.Kind.REFUND, change=message.credits, message=message, note=_("Not delivered"))
    return message


def deliver_waiting(limit=200, *, everywhere=False):
    """Send what is waiting, oldest first: this pharmacy's, or (everywhere) every pharmacy's for the worker."""
    manager = Message.all_objects if everywhere else Message.objects
    done = [deliver(m) for m in manager.select_related("tenant").filter(status=S.QUEUED).order_by("id")[:limit]]
    return done


def retry(message, *, user=None):
    """Send a failed message again as a new one."""
    return queue(channel=message.channel, kind=message.kind, to=message.to, body=message.body, subject=message.subject,
                 html=message.html, customer=message.customer, sale=message.sale, campaign=message.campaign, user=user)


def send_test(*, channel, to, user=None):
    from apps.core.tenancy import get_current_tenant

    shop = get_current_tenant().name
    body = _("Test message from %(shop)s. Your MediPOS messages work.") % {"shop": shop}
    return queue(channel=channel, kind=Message.Kind.TEST, to=to, body=body, subject=_("Test from %(shop)s") % {"shop": shop}, user=user)


# ---- Filling in messages ------------------------------------------------------------------------

def sms_money(value, shop):
    """Plain 'Tk 1,250' (the ৳ sign would make the SMS Unicode, more than twice the price)."""
    code = "Tk" if (shop.currency_code or "BDT").upper() == "BDT" else shop.currency_code
    text = f"{Decimal(value or 0).quantize(Decimal('0.01')):,.2f}"
    return f"{code} {text[:-3] if text.endswith('.00') else text}"


def _sign(raw):
    digest = hmac.new(django_settings.SECRET_KEY.encode(), f"bill:{raw}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest)[:12].decode()


def bill_token(sale):
    raw = f"{sale.tenant_id}-{sale.pk}"
    return f"{raw}-{_sign(raw)}"


def read_bill_token(token):
    """(tenant id, sale id) from a bill link, or None when it was changed."""
    parts = (token or "").split("-", 2)
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    if not hmac.compare_digest(_sign(f"{parts[0]}-{parts[1]}"), parts[2]):
        return None
    return int(parts[0]), int(parts[1])


def bill_url(sale, base_url=None):
    return (base_url or django_settings.SITE_URL).rstrip("/") + reverse("public_bill", args=[bill_token(sale)])


def bill_values(sale, base_url=None):
    shop = get_business_settings(sale.tenant)
    customer = sale.customer
    points = Customer.objects.filter(pk=sale.customer_id).values_list("points", flat=True).first() if customer else 0
    return {
        "name": customer.name if customer else _("Customer"), "shop": sale.tenant.name, "shop_phone": sale.outlet.phone or sale.tenant.phone,
        "invoice": sale.number, "date": timezone.localtime(sale.sold_at).strftime("%d/%m/%Y"), "total": sms_money(sale.total, shop),
        "paid": sms_money(sale.paid, shop), "due": sms_money(max(sale.due, ZERO), shop), "points": sale.points_earned,
        "points_balance": points or 0, "link": bill_url(sale, base_url),
    }


def customer_values(customer, shop=None):
    """For due reminders and campaigns. `customer` may carry an annotated `balance`."""
    from apps.core.tenancy import get_current_tenant
    from apps.customers.services import balance

    tenant = get_current_tenant()
    shop = shop or get_business_settings(tenant)
    owed = customer.balance if hasattr(customer, "balance") else balance(customer)
    return {"name": customer.name, "shop": tenant.name, "shop_phone": tenant.phone, "due": sms_money(max(owed, ZERO), shop),
            "points_balance": customer.points}


# ---- Bills ---------------------------------------------------------------------------------------

def send_bill(sale, *, channel, to=None, user=None, base_url=None, values=None, soon=True):
    """Send the bill to the customer (or to `to`, e.g. a walk-in's number)."""
    ms = message_settings()
    values = values or bill_values(sale, base_url)
    text = fill(ms.invoice_text, values)
    customer = sale.customer
    if channel == Channel.EMAIL:
        to = to or (customer.email if customer else "")
        subject = _("Your bill %(number)s from %(shop)s") % {"number": sale.number, "shop": sale.tenant.name}
        html = render_to_string("messaging/email/bill.html", {"sale": sale, "values": values, "text": text})
        return queue(channel=channel, kind=Message.Kind.INVOICE, to=to, subject=subject, body=text, html=html, customer=customer,
                     sale=sale, user=user, soon=soon, ms=ms)
    to = to or (customer.phone if customer else "")
    return queue(channel=channel, kind=Message.Kind.INVOICE, to=to, body=text, customer=customer, sale=sale, user=user, soon=soon, ms=ms)


def bill_whatsapp_link(sale, *, to=None, base_url=None):
    phone = normalize_phone(to or (sale.customer.phone if sale.customer else ""))
    return gateways.whatsapp_link(phone, fill(message_settings().invoice_text, bill_values(sale, base_url))) if phone else ""


def after_sale(sale, *, base_url=None, user=None):
    """What goes out by itself after a POS sale, and a WhatsApp link for the "sale complete" screen."""
    result = {"sent": [], "whatsapp": ""}
    if sale.customer_id is None:
        return result
    ms = message_settings()
    values = bill_values(sale, base_url)
    customer = sale.customer
    phone = normalize_phone(customer.phone)
    if phone:
        result["whatsapp"] = gateways.whatsapp_link(phone, fill(ms.invoice_text, values))
    wanted = ((Channel.SMS, ms.invoice_sms and customer.phone), (Channel.WHATSAPP, ms.invoice_whatsapp and ms.whatsapp_url and customer.phone),
              (Channel.EMAIL, ms.invoice_email and customer.email))
    for channel, go in wanted:
        if go:
            message = send_bill(sale, channel=channel, user=user, values=values)
            if message.status != S.FAILED:
                result["sent"].append(_("%(channel)s to %(to)s") % {"channel": Channel(channel).label, "to": message.to})
    return result


# ---- Due reminders --------------------------------------------------------------------------------

def due_customers(ms=None, *, fresh_only=True):
    """Customers owing at least the minimum, most first; with `fresh_only`, not reminded in the last N days."""
    ms = ms or message_settings()
    queryset = with_balances(Customer.objects.filter(is_active=True)).filter(balance__gte=max(ms.due_min_amount, Decimal("0.01")))
    if fresh_only:
        since = timezone.now() - timedelta(days=ms.due_every_days)
        recent = Message.objects.filter(customer=OuterRef("pk"), kind=Message.Kind.DUE, created_at__gte=since).exclude(status=S.FAILED)
        queryset = queryset.annotate(reminded=Exists(recent)).filter(reminded=False)
    return queryset.order_by("-balance", "name")


def send_due_reminders(customers, *, channel=Channel.SMS, user=None):
    ms = message_settings()
    shop = get_business_settings(ms.tenant)
    sent = []
    for customer in customers:
        to = customer.email if channel == Channel.EMAIL else customer.phone
        if not to:
            continue
        text = fill(ms.due_text, customer_values(customer, shop))
        sent.append(queue(channel=channel, kind=Message.Kind.DUE, to=to, body=text, customer=customer, user=user, soon=False, ms=ms,
                          subject=_("Your due at %(shop)s") % {"shop": ms.tenant.name}))
    send_soon(sent)
    return sent


# ---- The daily stock alert -------------------------------------------------------------------------

def stock_summary(today=None, days=30, limit=40):
    """Low and out-of-stock medicines and batches expiring within `days` (or expired), across active outlets."""
    from apps.items.models import Medicine
    from apps.outlets.models import Outlet
    from apps.stock.models import BatchStock

    today = today or timezone.localdate()
    outlets = list(Outlet.objects.filter(is_active=True).values_list("pk", flat=True))
    on_hand = (BatchStock.objects.filter(medicine=OuterRef("pk"), outlet_id__in=outlets, quantity__gt=0)
               .order_by().values("medicine").annotate(q=Sum("quantity")).values("q"))
    low = (Medicine.objects.filter(is_active=True, alert_quantity__gt=0)
           .annotate(on_hand=Coalesce(Subquery(on_hand, output_field=IntegerField()), Value(0)))
           .filter(on_hand__lte=F("alert_quantity")).order_by("on_hand", "name"))
    batches = (BatchStock.objects.filter(outlet_id__in=outlets, quantity__gt=0, batch__expiry_date__isnull=False,
                                         batch__expiry_date__lte=today + timedelta(days=days))
               .select_related("batch", "medicine", "outlet").order_by("batch__expiry_date"))
    return {
        "low": low.filter(on_hand__gt=0).count(), "out": low.filter(on_hand=0).count(),
        "expiring": batches.filter(batch__expiry_date__gt=today).count(), "expired": batches.filter(batch__expiry_date__lte=today).count(),
        "days": days, "low_rows": list(low[:limit]), "expiry_rows": list(batches[:limit]), "today": today,
    }


def _split(value):
    return [part.strip() for part in (value or "").replace(";", ",").split(",") if part.strip()]


def send_daily_alert(ms=None, *, today=None, base_url=None, user=None):
    """The morning summary to the shop's alert mobiles and emails. Nothing goes when there is nothing to report."""
    ms = ms or message_settings()
    summary = stock_summary(today, ms.alert_expiry_days)
    if not any(summary[k] for k in ("low", "out", "expiring", "expired")):
        return []
    base = (base_url or django_settings.SITE_URL).rstrip("/")
    values = {**summary, "shop": ms.tenant.name, "link": base + reverse("stock:low")}
    text = _("%(shop)s stock alert: %(low)s low, %(out)s out of stock, %(expiring)s batches expire within %(days)s days, "
             "%(expired)s expired. %(link)s") % values
    sent = [queue(channel=Channel.SMS, kind=Message.Kind.ALERT, to=phone, body=text, user=user, soon=False, ms=ms)
            for phone in _split(ms.alert_phones)]
    if _split(ms.alert_emails):
        html = render_to_string("messaging/email/stock_alert.html", {**values, "expiry_link": base + reverse("stock:expiring")})
        subject = _("%(shop)s: stock alert for %(date)s") % {"shop": ms.tenant.name, "date": summary["today"].strftime("%d %b %Y")}
        sent += [queue(channel=Channel.EMAIL, kind=Message.Kind.ALERT, to=email, subject=subject, body=text, html=html, user=user,
                       soon=False, ms=ms) for email in _split(ms.alert_emails)]
    send_soon(sent)
    return sent


def run_daily(now=None, *, force=False, base_url=None, user=None):
    """Once a day, from the set hour: the stock alert and due reminders, whichever are on. `force` runs it again now."""
    now = timezone.localtime(now or timezone.now())
    ms = message_settings()
    if not force and (ms.last_daily_run == now.date() or now.hour < ms.alert_hour):
        return None
    done = {"alerts": [], "reminders": []}
    if ms.daily_alerts:
        done["alerts"] = send_daily_alert(ms, today=now.date(), base_url=base_url, user=user)
    if ms.due_reminders and channel_ready(Channel.SMS, ms)[0]:
        done["reminders"] = send_due_reminders(list(due_customers(ms)), channel=Channel.SMS, user=user)
    ms.last_daily_run = now.date()
    ms.save(update_fields=["last_daily_run", "updated_at"])
    return done


# ---- Campaigns --------------------------------------------------------------------------------------

def recipients(campaign):
    """Customers the campaign goes to: its audience, less those who want no offers or have no number/email for its channel."""
    from apps.sales.models import Sale

    A = Campaign.Audience
    queryset = with_balances(Customer.objects.filter(is_active=True, no_marketing=False))
    since = timezone.now() - timedelta(days=campaign.days)
    if campaign.audience == A.DUE:
        queryset = queryset.filter(balance__gt=0)
    elif campaign.audience == A.RECENT:
        queryset = queryset.filter(Exists(Sale.objects.filter(customer=OuterRef("pk"), sold_at__gte=since)))
    elif campaign.audience == A.LAPSED:
        last = Sale.objects.filter(customer=OuterRef("pk")).order_by("-sold_at").values("sold_at")[:1]
        queryset = queryset.annotate(last_bought=Subquery(last)).filter(last_bought__lt=since)
    elif campaign.audience == A.POINTS:
        queryset = queryset.filter(points__gte=campaign.min_points)
    people = list(queryset.order_by("name"))
    if campaign.channel == Channel.EMAIL:
        return [c for c in people if "@" in (c.email or "")]
    return [c for c in people if normalize_phone(c.phone)]


def preview(campaign):
    """Who it reaches, what the first one reads, and (SMS) what it costs."""
    people = recipients(campaign)
    shop = get_business_settings(campaign.tenant)
    texts = [fill(campaign.text, customer_values(c, shop)) for c in people]
    sample = texts[0] if texts else fill(campaign.text, {"name": _("Customer"), "shop": campaign.tenant.name})
    return {"count": len(people), "sample": sample, "parts": sms_parts(sample),
            "credits": sum(sms_parts(t) for t in texts) if campaign.channel == Channel.SMS else 0, "people": people[:8]}


@transaction.atomic
def send_campaign(campaign, *, user=None):
    campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
    if not campaign.is_draft:
        raise MessageError(_("This campaign was already sent."))
    ms = message_settings()
    ready, why = channel_ready(campaign.channel, ms)
    if not ready:
        raise MessageError(why)
    people = recipients(campaign)
    if not people:
        raise MessageError(_("Nobody in this audience can get it."))
    shop = get_business_settings(campaign.tenant)
    texts = [(c, fill(campaign.text, customer_values(c, shop))) for c in people]
    if campaign.channel == Channel.SMS and ms.sms_source == MessageSettings.SmsSource.PLATFORM:
        need, left = sum(sms_parts(t) for _c, t in texts), credit_balance()
        if need > left:
            raise MessageError(_("This campaign needs %(need)s SMS credits and %(left)s are left.") % {"need": need, "left": left})
    sent = [queue(channel=campaign.channel, kind=Message.Kind.CAMPAIGN, to=c.email if campaign.channel == Channel.EMAIL else c.phone,
                  body=text, subject=campaign.subject or campaign.name, customer=c, campaign=campaign, user=user, soon=False, ms=ms)
            for c, text in texts]
    campaign.status, campaign.sent_at = Campaign.Status.SENT, timezone.now()
    campaign.save(update_fields=["status", "sent_at", "updated_at"])
    send_soon(sent)
    return sent
