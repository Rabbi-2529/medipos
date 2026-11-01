from django.urls import path

from . import views

app_name = "backup"

urlpatterns = [
    path("", views.data_page, name="data"),
]
