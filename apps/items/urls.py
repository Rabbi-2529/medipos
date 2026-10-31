from django.urls import path

from . import import_views, setup_views, tools_views, views

app_name = "items"

urlpatterns = [
    path("", views.medicine_list, name="list"),
    path("data/", views.medicine_data, name="data"),
    path("export.xlsx", views.medicine_export, name="export"),
    path("new/", views.medicine_create, name="create"),
    path("<int:pk>/edit/", views.medicine_edit, name="edit"),
    path("<int:pk>/toggle/", views.medicine_toggle, name="toggle"),
    path("import/", import_views.import_upload, name="import"),
    path("import/preview/", import_views.import_preview, name="import_preview"),
    path("import/template.xlsx", import_views.import_template, name="import_template"),
    path("catalog/", tools_views.catalog_browse, name="catalog"),
    path("catalog/data/", tools_views.catalog_data, name="catalog_data"),
    path("catalog/add/", tools_views.catalog_adopt, name="catalog_adopt"),
    path("bulk-price/", tools_views.bulk_price, name="bulk_price"),
    path("labels/", tools_views.labels, name="labels"),
    path("lookup/<slug:kind>/", setup_views.lookup, name="lookup"),
]

# Setup lists: /items/setup/categories/ etc. -> names items:categories, items:categories_data, …
for kind in setup_views.MASTERS:
    urlpatterns += [
        path(f"setup/{kind}/", setup_views.master_list, {"kind": kind}, name=kind),
        path(f"setup/{kind}/data/", setup_views.master_data, {"kind": kind}, name=f"{kind}_data"),
        path(f"setup/{kind}/new/", setup_views.master_create, {"kind": kind}, name=f"{kind}_create"),
        path(f"setup/{kind}/<int:pk>/edit/", setup_views.master_edit, {"kind": kind}, name=f"{kind}_edit"),
        path(f"setup/{kind}/<int:pk>/toggle/", setup_views.master_toggle, {"kind": kind}, name=f"{kind}_toggle"),
    ]
