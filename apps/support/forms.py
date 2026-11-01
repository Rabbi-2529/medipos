from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .models import SupportSettings

# Starting points for the admin; every field stays editable. Free tiers and model names change: check the provider's page.
PRESETS = [
    ("groq", "Groq (free plan)", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    ("openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1", ""),
    ("gemini", "Google Gemini (OpenAI-compatible)", "https://generativelanguage.googleapis.com/v1beta/openai", ""),
    ("ollama", "Ollama on your own server (free, no key)", "http://localhost:11434/v1", "llama3.2"),
    ("openai", "OpenAI", "https://api.openai.com/v1", ""),
]


class SupportSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SupportSettings
        fields = ["enabled", "engine", "base_url", "model", "api_key", "temperature", "max_tokens", "daily_limit", "welcome", "contact",
                  "instructions"]
        widgets = {"api_key": forms.PasswordInput(render_value=True, attrs={"autocomplete": "off"}),
                   "instructions": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["engine"].widget.attrs.update({"data-mp-select": "", "data-no-search": ""})

    def clean_base_url(self):
        url = self.cleaned_data["base_url"].strip().rstrip("/")
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        if url and not url.lower().startswith(("http://", "https://")):
            raise forms.ValidationError(_("Start with https:// (or http:// for a server on your network)."))
        return url

    def clean_temperature(self):
        value = self.cleaned_data["temperature"]
        if not Decimal("0") <= value <= Decimal("2"):
            raise forms.ValidationError(_("Between 0 and 2."))
        return value

    def clean(self):
        data = super().clean()
        if data.get("engine") == SupportSettings.Engine.AI and not (data.get("base_url") and data.get("model")):
            raise forms.ValidationError(_("The AI engine needs the API base URL and the model."))
        return data
