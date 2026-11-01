"""Phone numbers, SMS length and filling in message templates."""
import re

# GSM 03.38: these fit 7-bit SMS (160 a part); anything else (Bangla, emoji) makes the whole SMS Unicode (70 a part).
GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENDED = set("^{}\\[~]|€")  # each takes two places


def normalize_phone(value):
    """A Bangladeshi mobile in any common form -> 8801XXXXXXXXX; other international numbers -> digits; else ''."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if re.fullmatch(r"01[3-9]\d{8}", digits):
        return "88" + digits
    if re.fullmatch(r"8801[3-9]\d{8}", digits):
        return digits
    if digits.startswith("880"):
        return ""  # a Bangladeshi number of the wrong length
    return digits if 10 <= len(digits) <= 15 and not digits.startswith("0") else ""


def sms_parts(text):
    """How many SMS a text costs: 160/153 characters a part in GSM, 70/67 in Unicode."""
    text = text or ""
    if all(c in GSM_BASIC or c in GSM_EXTENDED for c in text):
        length, single, multi = sum(2 if c in GSM_EXTENDED else 1 for c in text), 160, 153
    else:
        length, single, multi = len(text.encode("utf-16-le")) // 2, 70, 67
    if length == 0:
        return 0
    return 1 if length <= single else -(-length // multi)


def is_unicode(text):
    return not all(c in GSM_BASIC or c in GSM_EXTENDED for c in text or "")


PLACEHOLDER = re.compile(r"\{(\w+)\}")


def fill(template, values):
    """'{name}, your bill {invoice} is {total}.' with values. An unknown {word} stays as typed, so a typo shows in the preview."""
    def one(match):
        value = values.get(match.group(1), match.group(0))
        return "" if value is None else str(value)

    return PLACEHOLDER.sub(one, template or "")


def placeholders(template):
    """The {words} used in a template, in order."""
    return PLACEHOLDER.findall(template or "")
