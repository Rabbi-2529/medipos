from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.stock_list, name="list"),
    path("data/", views.stock_data, name="data"),
    path("medicine/<int:pk>/", views.stock_card, name="card"),
    path("medicine/<int:pk>/movements/", views.movement_data, name="movements"),
    path("opening/", views.opening_upload, name="opening"),
    path("opening/preview/", views.opening_preview, name="opening_preview"),
    path("opening/template.xlsx", views.opening_template, name="opening_template"),
]
