from django.urls import path

from . import offline, views

app_name = "sales"

urlpatterns = [
    path("pos/", views.pos, name="pos"),
    path("pos/search/", views.pos_search, name="pos_search"),
    path("pos/batches/", views.pos_batches, name="pos_batches"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("pos/offline-data/", offline.offline_data, name="pos_offline_data"),
    path("pos/sync/", offline.sync, name="pos_sync"),
    path("pos/hold/", views.pos_hold, name="pos_hold"),
    path("pos/held/", views.pos_held, name="pos_held"),
    path("pos/recall/", views.pos_recall, name="pos_recall"),
    path("pos/quote/", views.pos_quote, name="pos_quote"),
    path("quotations/", views.quotation_list, name="quotations"),
    path("quotations/data/", views.quotation_data, name="quotation_data"),
    path("quotations/<int:pk>/", views.quotation_detail, name="quotation"),
    path("quotations/<int:pk>/print/", views.quotation_print, name="quotation_print"),
    path("quotations/<int:pk>/delete/", views.quotation_delete, name="quotation_delete"),
    path("promotions/", views.promotion_list, name="promotions"),
    path("promotions/new/", views.PromotionCreateView.as_view(), name="promotion_create"),
    path("promotions/<int:pk>/edit/", views.PromotionUpdateView.as_view(), name="promotion_edit"),
    path("promotions/<int:pk>/toggle/", views.promotion_toggle, name="promotion_toggle"),
    path("promotions/<int:pk>/delete/", views.promotion_delete, name="promotion_delete"),
    path("registers/", views.register_list, name="registers"),
    path("registers/open/", views.register_open, name="register_open"),
    path("registers/close/", views.register_close_current, name="register_close"),
    path("registers/<int:pk>/", views.register_detail, name="register"),
    path("", views.sale_list, name="list"),
    path("data/", views.sale_data, name="data"),
    path("<int:pk>/", views.sale_detail, name="detail"),
    path("<int:pk>/receipt/", views.receipt, name="receipt"),
    path("<int:pk>/return/", views.return_create, name="return_create"),
    path("returns/", views.return_list, name="returns"),
    path("returns/data/", views.return_data, name="return_data"),
]
