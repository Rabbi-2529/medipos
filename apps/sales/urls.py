from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("pos/", views.pos, name="pos"),
    path("pos/search/", views.pos_search, name="pos_search"),
    path("pos/batches/", views.pos_batches, name="pos_batches"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("pos/hold/", views.pos_hold, name="pos_hold"),
    path("pos/held/", views.pos_held, name="pos_held"),
    path("pos/recall/", views.pos_recall, name="pos_recall"),
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
