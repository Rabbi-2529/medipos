from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.audit_log, name="log"),
    path("data/", views.audit_data, name="data"),
    path("<int:pk>/", views.audit_entry, name="entry"),
    path("branches/", views.branch_audits, name="branches"),
    path("branches/<int:pk>/", views.branch_audit_detail, name="branch"),
    path("branches/<int:pk>/print/", views.branch_audit_print, name="branch_print"),
]
