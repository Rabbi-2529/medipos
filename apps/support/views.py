import json

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.people.access import has_perm, shop_permission_required

from .assistant import AssistantError, ask, test_connection
from .forms import PRESETS, SupportSettingsForm
from .models import ChatMessage, ChatThread, SupportSettings


def widget_context(request):
    """Context processor: show the chat button only when the platform switched it on and the person's role allows it."""
    tenant = getattr(request, "tenant", None)
    if tenant is None or not request.user.is_authenticated:
        return {}
    config = SupportSettings.load()
    return {"support_widget": config.enabled and has_perm(request.user, "support.list")}


def _config():
    config = SupportSettings.load()
    if not config.enabled:
        raise Http404
    return config


def _message(m):
    return {"role": m.role, "content": m.content, "source": m.source, "at": timezone.localtime(m.created_at).strftime("%H:%M")}


@shop_permission_required("support.list")
def chat_state(request):
    """The person's latest conversation, for the chat panel."""
    config = _config()
    thread = ChatThread.objects.filter(user=request.user).first()
    return JsonResponse({
        "thread": thread.pk if thread else None,
        "messages": [_message(m) for m in thread.messages.all()] if thread else [],
        "welcome": config.welcome or _("Hello! Ask me how to do something in MediPOS."),
        "contact": config.contact,
    })


@shop_permission_required("support.list")
@require_POST
def chat_send(request):
    _config()
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = {}
    thread = None
    if str(data.get("thread") or "").isdigit():
        thread = ChatThread.objects.filter(pk=data["thread"], user=request.user).first()
    try:
        thread, answer = ask(tenant=request.tenant, user=request.user, question=str(data.get("message") or ""), thread=thread,
                             page=str(data.get("page") or "")[:200])
    except AssistantError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"thread": thread.pk, "answer": _message(answer)})


# ---- Platform console ------------------------------------------------------------------------

def platform_settings(request):
    """Wrapped by the console's platform_admin_required in console/urls.py."""
    config = SupportSettings.load()
    form = SupportSettingsForm(request.POST or None, instance=config)
    if request.method == "POST":
        if request.POST.get("action") == "test":
            ok, text = test_connection(config)
            (messages.success if ok else messages.error)(request, _("Answer: %(text)s") % {"text": text[:300]})
            return redirect("platform:support")
        if form.is_valid():
            config = form.save(commit=False)
            config.updated_by = request.user
            config.save()
            messages.success(request, _("Support assistant settings saved."))
            return redirect("platform:support")
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    answers = ChatMessage.all_objects.filter(role=ChatMessage.Role.ASSISTANT)
    stats = answers.filter(created_at__gte=today).aggregate(total=Count("id"), ai=Count("id", filter=Q(source=ChatMessage.Source.AI)))
    recent = (ChatMessage.all_objects.filter(role=ChatMessage.Role.USER).select_related("tenant", "thread__user")
              .order_by("-created_at")[:30])
    return render(request, "support/platform_settings.html", {"form": form, "config": config, "stats": stats, "recent": recent,
                                                               "presets": PRESETS})
