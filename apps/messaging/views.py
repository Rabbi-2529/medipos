from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape, format_html
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.datatables import datatable_json
from apps.core.tenancy import tenant_context
from apps.customers.models import Customer
from apps.customers.services import balance
from apps.people.access import has_perm, shop_permission_required
from apps.sales.models import Sale
from apps.setup.services import get_business_settings
from apps.tenants.models import Tenant

from . import loyalty
from .forms import (
    BILL_WORDS, CUSTOMER_WORDS, AdjustPointsForm, CampaignForm, LoyaltyForm, MessageSettingsForm, SendForm, TestMessageForm,
)
from .gateways import whatsapp_link
from .models import Campaign, Channel, Message, MessageSettings, PointEntry, SmsCredit
from .services import (
    MessageError, bill_whatsapp_link, channel_ready, credit_balance, customer_values, deliver_waiting, due_customers,
    message_settings, preview, queue, read_bill_token, retry, run_daily, send_bill, send_campaign, send_due_reminders, send_test,
)
from .text import fill, normalize_phone

STATUS_TONE = {Message.Status.QUEUED: "amber", Message.Status.SENT: "teal", Message.Status.FAILED: "red"}
STATUS_ICON = {Message.Status.QUEUED: "bi-hourglass-split", Message.Status.SENT: "bi-check-circle", Message.Status.FAILED: "bi-x-circle"}
CHANNEL_ICON = {Channel.SMS: "bi-chat-left-text", Channel.WHATSAPP: "bi-whatsapp", Channel.EMAIL: "bi-envelope"}


def _month_start():
    return timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _uses_credits(ms):
    return ms.sms_source == MessageSettings.SmsSource.PLATFORM


# ---- Outbox ------------------------------------------------------------------------------

@shop_permission_required("message.list")
def outbox(request):
    ms = message_settings()
    can_send = has_perm(request.user, "message.create")
    form = TestMessageForm(request.POST or None, prefix="test") if can_send else None
    if request.method == "POST":
        if not can_send:
            raise Http404
        action = request.POST.get("action")
        if action == "test" and form.is_valid():
            message = send_test(channel=form.cleaned_data["channel"], to=form.cleaned_data["to"], user=request.user)
            if message.status == Message.Status.FAILED:
                messages.error(request, _("Not sent: %(why)s") % {"why": message.detail})
            else:
                messages.success(request, _("Test message to %(to)s is on its way. Its result shows in the list.") % {"to": message.to})
            return redirect("messaging:outbox")
        if action == "send_waiting":
            done = deliver_waiting(limit=50)
            messages.success(request, _("%(sent)s of %(count)s waiting messages sent.") % {
                "sent": sum(1 for m in done if m.status == Message.Status.SENT), "count": len(done)})
            return redirect("messaging:outbox")
        if action == "retry":
            failed = get_object_or_404(Message, pk=request.POST.get("pk"), status=Message.Status.FAILED)
            again = retry(failed, user=request.user)
            if again.status == Message.Status.FAILED:
                messages.error(request, _("Not sent: %(why)s") % {"why": again.detail})
            else:
                messages.success(request, _("Sent again to %(to)s.") % {"to": again.to})
            return redirect("messaging:outbox")
    month = Message.objects.filter(created_at__gte=_month_start())
    counts = month.aggregate(sent=Count("id", filter=Q(status=Message.Status.SENT)), failed=Count("id", filter=Q(status=Message.Status.FAILED)),
                             credits=Sum("credits", filter=Q(status=Message.Status.SENT)))
    return render(request, "messaging/outbox.html", {
        "ms": ms, "uses_credits": _uses_credits(ms), "credits": credit_balance(), "counts": counts, "form": form, "can_send": can_send,
        "waiting": Message.objects.filter(status=Message.Status.QUEUED).count(),
        "channels": Channel.choices, "statuses": Message.Status.choices, "kinds": Message.Kind.choices,
        "sms_ready": channel_ready(Channel.SMS, ms),
    })


def _message_row(m, can_send, token):
    status = format_html('<span class="att-chip tone-{}"><i class="bi {}"></i> {}</span>', STATUS_TONE[m.status], STATUS_ICON[m.status],
                         m.get_status_display())
    detail = format_html('<small class="d-block text-body-secondary msg-detail">{}</small>', m.detail) if m.detail and m.status == Message.Status.FAILED else ""
    to = escape(m.to)
    if m.customer_id:
        to = format_html('<a href="{}">{}</a><small class="d-block text-body-secondary">{}</small>',
                         reverse("customers:detail", args=[m.customer_id]), m.customer.name, m.to)
    about = m.get_kind_display()
    if m.sale_id:
        about = format_html('{} <a href="{}">{}</a>', about, reverse("sales:detail", args=[m.sale_id]), m.sale.number)
    elif m.campaign_id:
        about = format_html('{} <a href="{}">{}</a>', about, reverse("messaging:campaign", args=[m.campaign_id]), m.campaign.name)
    action = ""
    if can_send and m.status == Message.Status.FAILED:
        action = format_html('<form method="post" action="{}"><input type="hidden" name="csrfmiddlewaretoken" value="{}">'
                             '<input type="hidden" name="action" value="retry"><input type="hidden" name="pk" value="{}">'
                             '<button class="btn btn-sm btn-outline-primary" type="submit">{}</button></form>',
                             reverse("messaging:outbox"), token, m.pk, _("Retry"))
    return [
        date_format(timezone.localtime(m.created_at), "d M Y, h:i A"),
        format_html('<i class="bi {}"></i> {}', CHANNEL_ICON[m.channel], m.get_channel_display()),
        to, about,
        format_html('<div class="msg-body">{}</div>', m.subject + " — " + m.body if m.subject and m.channel == Channel.EMAIL else m.body),
        str(m.parts or "") if m.channel == Channel.SMS else "",
        format_html("{}{}", status, detail), action,
    ]


@shop_permission_required("message.list")
def outbox_data(request):
    from django.middleware.csrf import get_token

    token = get_token(request)  # the retry buttons post back
    queryset = Message.objects.select_related("customer", "sale", "campaign")
    for field in ("channel", "status", "kind"):
        value = request.GET.get(field, "")
        if value:
            queryset = queryset.filter(**{field: value})

    def search(qs, term):
        return qs.filter(Q(to__icontains=term) | Q(body__icontains=term) | Q(customer__name__icontains=term) | Q(sale__number__icontains=term))

    can_send = has_perm(request.user, "message.create")
    return datatable_json(request, queryset.order_by("-created_at", "-id"),
                          order_columns=["created_at", "channel", "to", "kind", None, "parts", "status", None],
                          row=lambda m: _message_row(m, can_send, token), search=search)


# ---- Settings ------------------------------------------------------------------------------

@shop_permission_required("message.edit")
def settings_view(request):
    ms = message_settings()
    form = MessageSettingsForm(request.POST or None, instance=ms)
    if request.method == "POST":
        if request.POST.get("action") == "run_daily":
            done = run_daily(force=True, base_url=request.build_absolute_uri("/"), user=request.user)
            messages.success(request, _("Done: %(alerts)s stock alerts and %(reminders)s due reminders sent out.") % {
                "alerts": len(done["alerts"]), "reminders": len(done["reminders"])})
            return redirect("messaging:settings")
        if form.is_valid():
            form.save()
            messages.success(request, _("Message settings saved."))
            return redirect("messaging:settings")
    return render(request, "messaging/settings.html", {
        "form": form, "ms": ms, "credits": credit_balance(), "history": SmsCredit.objects.select_related("message")[:12],
        "bill_words": BILL_WORDS, "customer_words": CUSTOMER_WORDS, "platform_gateway": bool(_platform_gateway()),
        "last_run": ms.last_daily_run,
    })


def _platform_gateway():
    from django.conf import settings

    return settings.SMS_GATEWAY_URL


# ---- Due reminders ---------------------------------------------------------------------------

@shop_permission_required("message.create")
def due_reminders(request):
    ms = message_settings()
    fresh = request.GET.get("all") != "1"
    people = list(due_customers(ms, fresh_only=fresh)[:300])
    shop = get_business_settings(request.tenant)
    if request.method == "POST":
        picked = {int(pk) for pk in request.POST.getlist("customer") if pk.isdigit()}
        channel = request.POST.get("channel", Channel.SMS)
        chosen = [c for c in due_customers(ms, fresh_only=False) if c.pk in picked]
        if channel not in Channel.values or not chosen:
            messages.error(request, _("Tick the customers to remind."))
            return redirect(request.get_full_path())
        ready, why = channel_ready(channel, ms)
        if not ready:
            messages.error(request, why)
            return redirect(request.get_full_path())
        sent = send_due_reminders(chosen, channel=channel, user=request.user)
        messages.success(request, _("%(count)s due reminders queued. Results show in the outbox.") % {"count": len(sent)})
        return redirect("messaging:outbox")
    for customer in people:
        text = fill(ms.due_text, customer_values(customer, shop))
        phone = normalize_phone(customer.phone)
        customer.whatsapp = whatsapp_link(phone, text) if phone else ""
        customer.reminder = text
    return render(request, "messaging/due_reminders.html", {
        "people": people, "fresh": fresh, "ms": ms, "total": sum((c.balance for c in people), 0),
        "channels": [(Channel.SMS, Channel.SMS.label), (Channel.EMAIL, Channel.EMAIL.label)] + (
            [(Channel.WHATSAPP, _("WhatsApp gateway"))] if ms.whatsapp_url else []),
    })


@shop_permission_required("message.create")
@require_POST
def remind_customer(request, pk):
    """One customer's due reminder from their page."""
    customer = get_object_or_404(Customer, pk=pk)
    customer.balance = balance(customer)
    form = SendForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Choose how to send it and to where."))
        return redirect("customers:detail", pk=pk)
    if customer.balance <= 0:
        messages.error(request, _("%(name)s owes nothing.") % {"name": customer.name})
        return redirect("customers:detail", pk=pk)
    message = queue(channel=form.cleaned_data["channel"], kind=Message.Kind.DUE, to=form.cleaned_data["to"], customer=customer,
                    body=fill(message_settings().due_text, customer_values(customer)), user=request.user,
                    subject=_("Your due at %(shop)s") % {"shop": request.tenant.name})
    if message.status == Message.Status.FAILED:
        messages.error(request, _("Not sent: %(why)s") % {"why": message.detail})
    else:
        messages.success(request, _("Due reminder sent to %(to)s.") % {"to": message.to})
    return redirect("customers:detail", pk=pk)


@shop_permission_required("message.create")
@require_POST
def sale_send(request, pk):
    """Send a bill by SMS, email or the WhatsApp gateway from the sale page."""
    sale = get_object_or_404(Sale.objects.select_related("customer", "outlet", "tenant"), pk=pk)
    form = SendForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Choose how to send it and to where."))
        return redirect("sales:detail", pk=pk)
    message = send_bill(sale, channel=form.cleaned_data["channel"], to=form.cleaned_data["to"], user=request.user,
                        base_url=request.build_absolute_uri("/"))
    if message.status == Message.Status.FAILED:
        messages.error(request, _("Not sent: %(why)s") % {"why": message.detail})
    else:
        messages.success(request, _("Bill %(number)s sent to %(to)s.") % {"number": sale.number, "to": message.to})
    return redirect("sales:detail", pk=pk)


def sale_panel(request, sale):
    """What the sale page's "Send the bill" card needs (None without the permission)."""
    if not has_perm(request.user, "message.create"):
        return None
    customer = sale.customer
    return {
        "form": SendForm(initial={"channel": Channel.SMS, "to": customer.phone if customer else ""}),
        "whatsapp": bill_whatsapp_link(sale, base_url=request.build_absolute_uri("/")),
        "email": customer.email if customer else "",
        "sent": list(sale.messages.order_by("-created_at")[:5]),
    }


def customer_panel(request, customer, owed):
    """The customer page's points and messages card."""
    prog = loyalty.program()
    can_send = has_perm(request.user, "message.create")
    panel = {
        "program": prog, "can_adjust": has_perm(request.user, "loyalty.edit"), "adjust_form": AdjustPointsForm(),
        "points": customer.point_entries.select_related("sale")[:8], "can_send": can_send,
        "messages": customer.messages.order_by("-created_at")[:6] if has_perm(request.user, "message.list") else None,
    }
    if can_send and owed > 0:
        customer.balance = owed
        text = fill(message_settings().due_text, customer_values(customer))
        phone = normalize_phone(customer.phone)
        panel.update({"remind_form": SendForm(initial={"channel": Channel.SMS, "to": customer.phone}),
                      "whatsapp": whatsapp_link(phone, text) if phone else ""})
    return panel


# ---- Campaigns ----------------------------------------------------------------------------------

@shop_permission_required("message.list")
def campaigns(request):
    rows = Campaign.objects.annotate(
        total=Count("messages"), sent=Count("messages", filter=Q(messages__status=Message.Status.SENT)),
        failed=Count("messages", filter=Q(messages__status=Message.Status.FAILED))).select_related("created_by")
    return render(request, "messaging/campaign_list.html", {"campaigns": rows})


@shop_permission_required("message.create")
def campaign_form(request, pk=None):
    campaign = get_object_or_404(Campaign, pk=pk, status=Campaign.Status.DRAFT) if pk else None
    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.created_by = campaign.created_by or request.user
        campaign.save()
        messages.success(request, _("“%(name)s” saved. Check who it reaches, then send it.") % {"name": campaign.name})
        return redirect("messaging:campaign", pk=campaign.pk)
    return render(request, "messaging/campaign_form.html", {"form": form, "campaign": campaign, "words": CUSTOMER_WORDS})


@shop_permission_required("message.list")
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign.objects.select_related("created_by"), pk=pk)
    can_send = has_perm(request.user, "message.create")
    if request.method == "POST":
        if not can_send:
            raise Http404
        if request.POST.get("action") == "delete" and campaign.is_draft:
            campaign.delete()
            messages.success(request, _("Draft deleted."))
            return redirect("messaging:campaigns")
        try:
            sent = send_campaign(campaign, user=request.user)
        except MessageError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("“%(name)s” is going out to %(count)s customers.") % {"name": campaign.name, "count": len(sent)})
        return redirect("messaging:campaign", pk=pk)
    ms = message_settings()
    context = {"campaign": campaign, "can_send": can_send, "ms": ms, "uses_credits": _uses_credits(ms), "credits": credit_balance()}
    if campaign.is_draft:
        context.update({"preview": preview(campaign), "ready": channel_ready(campaign.channel, ms)})
    else:
        context["stats"] = campaign.messages.aggregate(
            total=Count("id"), sent=Count("id", filter=Q(status=Message.Status.SENT)), failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            waiting=Count("id", filter=Q(status=Message.Status.QUEUED)), credits=Sum("credits", filter=Q(status=Message.Status.SENT)))
    return render(request, "messaging/campaign_detail.html", context)


# ---- Loyalty --------------------------------------------------------------------------------------

@shop_permission_required("loyalty.list")
def points(request):
    prog = loyalty.program()
    month = PointEntry.objects.filter(date__gte=_month_start().date())
    out = Customer.objects.filter(points__gt=0).aggregate(points=Sum("points"), people=Count("id"))
    return render(request, "messaging/points.html", {
        "program": prog, "outstanding": out["points"] or 0, "people": out["people"],
        "value": (out["points"] or 0) * prog.point_value,
        "earned": month.filter(change__gt=0).aggregate(t=Sum("change"))["t"] or 0,
        "spent": -(month.filter(kind=PointEntry.Kind.REDEEMED).aggregate(t=Sum("change"))["t"] or 0),
        "top": Customer.objects.filter(points__gt=0).order_by("-points")[:10], "kinds": PointEntry.Kind.choices,
    })


@shop_permission_required("loyalty.list")
def points_data(request):
    queryset = PointEntry.objects.select_related("customer", "sale", "created_by")
    if request.GET.get("kind") in PointEntry.Kind.values:
        queryset = queryset.filter(kind=request.GET["kind"])

    def search(qs, term):
        return qs.filter(Q(customer__name__icontains=term) | Q(customer__phone__icontains=term) | Q(sale__number__icontains=term))

    def row(e):
        tone = "teal" if e.change > 0 else "red"
        return [
            date_format(e.date, "d M Y"),
            format_html('<a href="{}">{}</a>', reverse("customers:detail", args=[e.customer_id]), e.customer.name),
            e.get_kind_display(),
            format_html('<a href="{}">{}</a>', reverse("sales:detail", args=[e.sale_id]), e.sale.number) if e.sale_id else escape(e.note),
            format_html('<span class="points-change tone-{}">{}{}</span>', tone, "+" if e.change > 0 else "", e.change),
            str(e.balance),
        ]

    return datatable_json(request, queryset.order_by("-date", "-id"), order_columns=["date", "customer__name", "kind", None, "change", "balance"],
                          row=row, search=search)


@shop_permission_required("loyalty.edit")
def loyalty_settings(request):
    prog = loyalty.program()
    form = LoyaltyForm(request.POST or None, instance=prog)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Loyalty settings saved."))
        return redirect("messaging:loyalty_settings")
    return render(request, "messaging/loyalty_settings.html", {"form": form, "program": prog, "shop": get_business_settings(request.tenant)})


@shop_permission_required("loyalty.edit")
@require_POST
def adjust_points(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = AdjustPointsForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Enter the points and why."))
        return redirect("customers:detail", pk=pk)
    try:
        entry = loyalty.adjust(customer, form.cleaned_data["change"], note=form.cleaned_data["note"], user=request.user)
    except loyalty.LoyaltyError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("%(name)s now has %(n)s points.") % {"name": customer.name, "n": entry.balance})
    return redirect("customers:detail", pk=pk)


# ---- The customer's own copy of a bill (link in the SMS) -------------------------------------------

def public_bill(request, token):
    """No login: the signed link is the key. Shows the same receipt the shop prints."""
    from apps.sales.views import _sale_context

    found = read_bill_token(token)
    tenant = Tenant.objects.filter(pk=found[0]).first() if found else None
    if tenant is None:
        raise Http404
    with tenant_context(tenant):
        sale = Sale.objects.select_related("customer", "outlet", "created_by").filter(pk=found[1]).first()
        if sale is None:
            raise Http404
        request.tenant = tenant  # the money tags read the shop's currency from here
        settings = get_business_settings(tenant)
        context = _sale_context(sale)
        context.update({"size": "80", "auto_print": False, "shop": settings, "tenant": tenant, "public": True,
                        "total_discount": sale.discount + sale.line_discount - sale.points_discount})
        return render(request, "sales/receipt.html", context)
