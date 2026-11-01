from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.audit_log, name="log"),
    path("data/", views.audit_data, name="data"),
    path("<int:pk>/", views.audit_entry, name="entry"),
]
