"""Where a message actually goes.

SMS and WhatsApp go through an HTTP gateway described by a URL with {to}, {message}, {sender} and {api_key} in it,
which fits the Bangladeshi providers (they all take the number, the text, a sender ID and a key on one URL).
With no URL the console gateway only logs, which is what development and the demo use."""
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

log = logging.getLogger("apps.messaging")
TIMEOUT = 15  # seconds


@dataclass
class Result:
    ok: bool
    detail: str = ""  # the provider's answer, or why it failed


class ConsoleGateway:
    """Pretends to send: logs the message. Used when no gateway URL is set."""

    name = "console"

    def send(self, to, text):
        log.info("SMS to %s: %s", to, text)
        return Result(True, "console")


class HttpGateway:
    name = "http"

    def __init__(self, url, *, method="GET", api_key="", sender="", success_text=""):
        self.url, self.method, self.api_key, self.sender = url.strip(), (method or "GET").upper(), api_key, sender
        self.success_text = success_text.strip()

    def _url(self, to, text):
        values = {"to": to, "message": text, "sender": self.sender, "api_key": self.api_key}
        url = self.url
        for key, value in values.items():
            url = url.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
        return url

    def send(self, to, text):
        url = self._url(to, text)
        data = None
        if self.method == "POST":
            # The same parameters, sent as a form instead of in the address.
            parts = urllib.parse.urlsplit(url)
            data = parts.query.encode()
            url = urllib.parse.urlunsplit(parts._replace(query=""))
        request = urllib.request.Request(url, data=data, method=self.method,
                                         headers={"User-Agent": "MediPOS", "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read(2000).decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return Result(False, f"HTTP {error.code}: {error.read(300).decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            return Result(False, str(getattr(error, "reason", error))[:300])
        if self.success_text and self.success_text not in body:
            return Result(False, body[:300])
        return Result(True, body[:300])


def platform_sms_gateway():
    """The SaaS operator's SMS account (used for pharmacies on platform SMS credits)."""
    if not settings.SMS_GATEWAY_URL:
        return ConsoleGateway()
    return HttpGateway(settings.SMS_GATEWAY_URL, method=settings.SMS_GATEWAY_METHOD, api_key=settings.SMS_GATEWAY_API_KEY,
                       sender=settings.SMS_GATEWAY_SENDER, success_text=settings.SMS_GATEWAY_SUCCESS_TEXT)


def send_email(*, to, subject, text, html="", from_name="", reply_to=""):
    """Through the platform's mail server, under the pharmacy's name, with replies going to the pharmacy."""
    sender = settings.DEFAULT_FROM_EMAIL
    if from_name:
        address = sender.rsplit("<", 1)[-1].rstrip(">").strip()
        sender = f'"{from_name.replace(chr(34), "")}" <{address}>'
    email = EmailMultiAlternatives(subject=subject, body=text, from_email=sender, to=[to], reply_to=[reply_to] if reply_to else None)
    if html:
        email.attach_alternative(html, "text/html")
    try:
        email.send()
    except Exception as error:  # noqa: BLE001 - any mail server failure is reported on the message
        return Result(False, str(error)[:300])
    return Result(True, "")


def whatsapp_link(phone, text):
    """Opens WhatsApp (app or web) with the message typed in, for staff to press send."""
    return f"https://wa.me/{phone}?text={urllib.parse.quote(text)}"
