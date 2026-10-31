from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.items.choices import DosageForm


class CatalogMedicine(TimeStampedModel):
    """One registered brand. Shared by all pharmacies, managed by ThingBD staff."""

    brand_name = models.CharField(_("brand name"), max_length=150)
    generic_name = models.CharField(_("generic"), max_length=255, blank=True)
    strength = models.CharField(_("strength"), max_length=60, blank=True)
    dosage_form = models.CharField(_("dosage form"), max_length=20, choices=DosageForm.choices, blank=True)
    manufacturer = models.CharField(_("manufacturer"), max_length=150, blank=True)
    pack_size = models.CharField(_("pack size"), max_length=60, blank=True, help_text=_("e.g. 10 x 10"))
    unit_price = models.DecimalField(_("MRP per unit"), max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    search_text = models.CharField(max_length=600, blank=True, editable=False, db_index=True)

    class Meta:
        ordering = ["brand_name", "strength"]
        constraints = [
            models.UniqueConstraint(
                fields=["brand_name", "strength", "dosage_form", "manufacturer"], name="uniq_catalog_medicine",
            ),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        form = self.get_dosage_form_display() if self.dosage_form else ""
        return " ".join(part for part in (self.brand_name, self.strength, str(form)) if part)

    def save(self, *args, **kwargs):
        self.search_text = catalog_search_text(self.brand_name, self.strength, self.dosage_form, self.generic_name, self.manufacturer)
        super().save(*args, **kwargs)


def catalog_search_text(*parts):
    return " ".join(p for p in parts if p).lower()[:600]
