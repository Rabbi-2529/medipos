from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("", views.outbox, name="outbox"),
    path("data/", views.outbox_data, name="outbox_data"),
    path("settings/", views.settings_view, name="settings"),
    path("due-reminders/", views.due_reminders, name="due_reminders"),
    path("customers/<int:pk>/remind/", views.remind_customer, name="remind_customer"),
    path("sales/<int:pk>/send/", views.sale_send, name="sale_send"),
    path("campaigns/", views.campaigns, name="campaigns"),
    path("campaigns/new/", views.campaign_form, name="campaign_create"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign"),
    path("campaigns/<int:pk>/edit/", views.campaign_form, name="campaign_edit"),
    path("points/", views.points, name="points"),
    path("points/data/", views.points_data, name="points_data"),
    path("points/settings/", views.loyalty_settings, name="loyalty_settings"),
    path("customers/<int:pk>/points/", views.adjust_points, name="adjust_points"),
]
