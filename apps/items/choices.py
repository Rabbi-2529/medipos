from django.db import models
from django.utils.translation import gettext_lazy as _


class DosageForm(models.TextChoices):
    TABLET = "tablet", _("Tablet")
    CAPSULE = "capsule", _("Capsule")
    SYRUP = "syrup", _("Syrup")
    SUSPENSION = "suspension", _("Suspension")
    DROPS = "drops", _("Drops")
    INJECTION = "injection", _("Injection")
    INFUSION = "infusion", _("Infusion (IV)")
    CREAM = "cream", _("Cream")
    OINTMENT = "ointment", _("Ointment")
    GEL = "gel", _("Gel")
    LOTION = "lotion", _("Lotion")
    SOLUTION = "solution", _("Solution")
    SPRAY = "spray", _("Spray")
    INHALER = "inhaler", _("Inhaler")
    SUPPOSITORY = "suppository", _("Suppository")
    POWDER = "powder", _("Powder")
    SACHET = "sachet", _("Sachet")
    GRANULES = "granules", _("Granules")
    MOUTHWASH = "mouthwash", _("Mouthwash")
    SHAMPOO = "shampoo", _("Shampoo")
    OTHER = "other", _("Other")


# Spellings seen in spreadsheets and catalogs -> DosageForm value.
DOSAGE_ALIASES = {
    "tab": "tablet", "tabs": "tablet", "tablets": "tablet", "chewable tablet": "tablet", "film coated tablet": "tablet",
    "cap": "capsule", "caps": "capsule", "capsules": "capsule",
    "syr": "syrup", "susp": "suspension", "pfs": "suspension", "powder for suspension": "suspension",
    "drop": "drops", "eye drops": "drops", "ear drops": "drops", "paediatric drops": "drops", "pediatric drops": "drops",
    "inj": "injection", "iv injection": "injection", "im injection": "injection", "ivf": "infusion", "iv infusion": "infusion",
    "oint": "ointment", "eye ointment": "ointment", "supp": "suppository", "suppositories": "suppository",
    "nasal spray": "spray", "oral solution": "solution", "nebuliser solution": "solution",
    "inhalation": "inhaler", "evohaler": "inhaler",
}


def parse_dosage_form(value):
    """Map free text ("Tab", "Eye Drops") to a DosageForm value, or "" if unknown/blank."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    if text in DosageForm.values:
        return text
    for choice in DosageForm:
        if text == str(choice.label).lower():
            return choice.value
    return DOSAGE_ALIASES.get(text, DosageForm.OTHER)
