from django.urls import path

from . import views

app_name = "setup"

urlpatterns = [
    path("", views.business_settings, name="settings"),
]
