from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("batches/", views.batches, name="batches"),
    path("damage/", views.write_off_list, name="damages"),
    path("damage/data/", views.write_off_data, name="damage_data"),
    path("damage/new/", views.write_off_create, name="damage_create"),
    path("damage/expired/", views.write_off_expired_view, name="damage_expired"),
    path("damage/<int:pk>/", views.write_off_detail, name="damage"),
    path("transfers/", views.transfer_list, name="transfers"),
    path("transfers/data/", views.transfer_data, name="transfer_data"),
    path("transfers/new/", views.transfer_create, name="transfer_create"),
    path("transfers/<int:pk>/", views.transfer_detail, name="transfer"),
    path("transfers/<int:pk>/receive/", views.transfer_receive, name="transfer_receive"),
    path("transfers/<int:pk>/cancel/", views.transfer_cancel, name="transfer_cancel"),
    path("counts/", views.count_list, name="counts"),
    path("counts/data/", views.count_data, name="count_data"),
    path("counts/new/", views.count_create, name="count_create"),
    path("counts/<int:pk>/", views.count_detail, name="count"),
]
