from django import forms

from .tenancy import get_current_tenant


class BootstrapFormMixin:
    """Adds Bootstrap 5 classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            else:
                css = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class TenantModelForm(BootstrapFormMixin, forms.ModelForm):
    """ModelForm for tenant-owned models.

    - Stamps the current tenant on new instances before validation, so per-tenant
      unique constraints (e.g. outlet name) come back as form errors instead of a 500.
    - Rebuilds choice querysets for tenant-owned relations: the ones Django builds at
      import time ran with no tenant active and are therefore empty.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.tenant_id:
            tenant = get_current_tenant()
            if tenant is not None:
                self.instance.tenant = tenant
        for field in self.fields.values():
            queryset = getattr(field, "queryset", None)
            if queryset is not None and hasattr(queryset.model, "all_objects") and queryset.query.is_empty():
                field.queryset = queryset.model.objects.all()

    def _get_validation_exclusions(self):
        exclude = super()._get_validation_exclusions()
        exclude.discard("tenant")
        return exclude
