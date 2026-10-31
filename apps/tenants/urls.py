from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
]
