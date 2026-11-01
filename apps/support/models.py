from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

CACHE_KEY = "support-settings"


class SupportSettings(models.Model):
    """The SaaS operator's settings for the in-app support assistant (one row, edited in the platform console)."""

    class Engine(models.TextChoices):
        BUILTIN = "builtin", _("Built-in help (free, no AI)")
        AI = "ai", _("AI through an OpenAI-compatible API")

    enabled = models.BooleanField(_("Show the support assistant"), default=False,
                                  help_text=_("Pharmacy staff see it only when their role has “Support assistant”."))
    engine = models.CharField(_("answers come from"), max_length=10, choices=Engine.choices, default=Engine.BUILTIN)
    base_url = models.CharField(_("API base URL"), max_length=300, blank=True, help_text=_("Ends before /chat/completions."))
    model = models.CharField(_("model"), max_length=120, blank=True)
    api_key = models.CharField(_("API key"), max_length=300, blank=True,
                               help_text=_("Empty = SUPPORT_AI_API_KEY from the server's .env. Local servers (Ollama) need none."))
    temperature = models.DecimalField(_("temperature"), max_digits=3, decimal_places=2, default=Decimal("0.30"))
    max_tokens = models.PositiveIntegerField(_("longest answer (tokens)"), default=700)
    daily_limit = models.PositiveIntegerField(_("AI answers per pharmacy per day"), default=200,
                                              help_text=_("Past it, answers come from the built-in help until tomorrow."))
    instructions = models.TextField(_("extra instructions for the AI"), blank=True)
    welcome = models.CharField(_("greeting"), max_length=300, blank=True)
    contact = models.CharField(_("how to reach a person"), max_length=300, blank=True,
                               help_text=_("e.g. Call or WhatsApp 01XXXXXXXXX, 10 AM–8 PM."))
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = _("support assistant settings")

    def __str__(self):
        return str(_("Support assistant settings"))

    @classmethod
    def load(cls):
        found = cache.get(CACHE_KEY)
        if found is None:
            found, _created = cls.objects.get_or_create(pk=1)
            cache.set(CACHE_KEY, found, 60)
        return found

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(CACHE_KEY)

    @property
    def key(self):
        return self.api_key or getattr(settings, "SUPPORT_AI_API_KEY", "")

    @property
    def uses_ai(self):
        return self.engine == self.Engine.AI and bool(self.base_url and self.model)


class ChatThread(TenantOwnedModel):
    """One conversation between a member of staff and the assistant."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    title = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"#{self.pk}"


class ChatMessage(TenantOwnedModel):
    class Role(models.TextChoices):
        USER = "user", _("Staff")
        ASSISTANT = "assistant", _("Assistant")

    class Source(models.TextChoices):
        HELP = "help", _("Built-in help")
        AI = "ai", _("AI")
        ERROR = "error", _("Error")

    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    source = models.CharField(max_length=6, choices=Source.choices, blank=True)
    page = models.CharField(max_length=200, blank=True)  # where the question was asked
    tokens = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at", "id"]
