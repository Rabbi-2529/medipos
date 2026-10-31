from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

from .choices import DosageForm


class MasterModel(TenantOwnedModel):
    """A named setup-list entry (category, generic, …), unique by name inside a pharmacy."""

    name = models.CharField(_("name"), max_length=150)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        abstract = True
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_%(app_label)s_%(class)s_name_per_tenant",
                                    violation_error_message=_("An entry with this name already exists.")),
        ]

    def __str__(self):
        return self.name


class Category(MasterModel):
    class Meta(MasterModel.Meta):
        verbose_name_plural = "categories"


class Generic(MasterModel):
    """Active ingredient(s), e.g. "Paracetamol" or "Paracetamol + Caffeine"."""


class Manufacturer(MasterModel):
    """Pharmaceutical company / brand owner."""


class Unit(MasterModel):
    """Piece, Strip, Box, Bottle, …"""


class Rack(MasterModel):
    """Shelf location inside the shop."""


def medicine_image_path(instance, filename):
    return f"tenants/{instance.tenant_id}/medicines/{filename}"


class Medicine(TenantOwnedModel):
    """Anything the pharmacy sells. Stock and prices are kept in the sale unit (usually a piece)."""

    class Kind(models.TextChoices):
        MEDICINE = "medicine", _("Medicine")
        GENERAL = "general", _("General item")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.MEDICINE)
    name = models.CharField(_("brand / item name"), max_length=150)
    strength = models.CharField(_("strength"), max_length=60, blank=True, help_text=_("e.g. 500 mg, 120 mg/5 ml"))
    dosage_form = models.CharField(_("dosage form"), max_length=20, choices=DosageForm.choices, blank=True)
    generic = models.ForeignKey(Generic, on_delete=models.PROTECT, null=True, blank=True, related_name="medicines", verbose_name=_("generic"))
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, null=True, blank=True, related_name="medicines", verbose_name=_("manufacturer"))
    category = models.ForeignKey(Category, on_delete=models.PROTECT, null=True, blank=True, related_name="medicines", verbose_name=_("category"))
    rack = models.ForeignKey(Rack, on_delete=models.SET_NULL, null=True, blank=True, related_name="medicines", verbose_name=_("rack"))
    alternative_name = models.CharField(_("alternative name"), max_length=150, blank=True, help_text=_("Local name or alias; searchable."))

    code = models.CharField(_("code"), max_length=30)
    barcode = models.CharField(_("barcode"), max_length=64, blank=True)

    sale_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+", verbose_name=_("sale unit"))
    purchase_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+", verbose_name=_("purchase unit"))
    conversion = models.PositiveIntegerField(
        _("sale units per purchase unit"), default=1, validators=[MinValueValidator(1)],
        help_text=_("e.g. 100 if a box holds 100 pieces"),
    )
    strip_size = models.PositiveSmallIntegerField(
        _("pieces per strip"), null=True, blank=True, validators=[MinValueValidator(1)],
        help_text=_("Lets the POS sell by strip."),
    )

    purchase_price = models.DecimalField(_("purchase price"), max_digits=14, decimal_places=4, default=Decimal("0"),
                                         validators=[MinValueValidator(0)], help_text=_("Per sale unit."))
    sale_price = models.DecimalField(_("sale price (MRP)"), max_digits=12, decimal_places=2, default=Decimal("0"),
                                     validators=[MinValueValidator(0)], help_text=_("Per sale unit."))
    wholesale_price = models.DecimalField(_("wholesale price"), max_digits=12, decimal_places=2, null=True, blank=True,
                                          validators=[MinValueValidator(0)])
    alert_quantity = models.PositiveIntegerField(_("low-stock alert at"), default=0, help_text=_("In sale units. 0 = no alert."))

    prescription_required = models.BooleanField(_("prescription required"), default=False)
    track_expiry = models.BooleanField(_("track batch & expiry"), default=True)
    image = models.ImageField(_("image"), upload_to=medicine_image_path, blank=True)
    description = models.TextField(_("notes"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    catalog_ref = models.ForeignKey("catalog.CatalogMedicine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    search_text = models.CharField(max_length=600, blank=True, editable=False, db_index=True)

    class Meta:
        ordering = ["name", "strength"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_medicine_code_per_tenant",
                                    violation_error_message=_("A medicine with this code already exists.")),
            models.UniqueConstraint(fields=["tenant", "barcode"], condition=~Q(barcode=""), name="uniq_medicine_barcode_per_tenant",
                                    violation_error_message=_("A medicine with this barcode already exists.")),
        ]
        indexes = [
            models.Index(fields=["tenant", "name"], name="medicine_tenant_name"),
            models.Index(fields=["tenant", "barcode"], name="medicine_tenant_barcode"),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        form = self.get_dosage_form_display() if self.dosage_form else ""
        return " ".join(part for part in (self.name, self.strength, str(form)) if part)

    @property
    def margin_percent(self):
        if not self.purchase_price:
            return None
        return round((self.sale_price - self.purchase_price) * 100 / self.purchase_price, 1)

    def save(self, *args, **kwargs):
        self.search_text = build_search_text(
            name=self.name, strength=self.strength, dosage_form=self.dosage_form,
            generic=self.generic.name if self.generic_id else "",
            manufacturer=self.manufacturer.name if self.manufacturer_id else "",
            alternative_name=self.alternative_name, code=self.code, barcode=self.barcode,
        )
        super().save(*args, **kwargs)


def build_search_text(**parts):
    return " ".join(str(v) for v in parts.values() if v).lower()[:600]
