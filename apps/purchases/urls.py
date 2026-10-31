from django.urls import path

from . import views

app_name = "purchases"

urlpatterns = [
    path("", views.purchase_list, name="list"),
    path("data/", views.purchase_data, name="data"),
    path("new/", views.purchase_create, name="create"),
    path("due/", views.due_lookup, name="due_lookup"),
    path("<int:pk>/", views.purchase_detail, name="detail"),
    path("<int:pk>/edit/", views.purchase_edit, name="edit"),
    path("<int:pk>/receive/", views.purchase_receive, name="receive"),
    path("<int:pk>/delete/", views.purchase_delete, name="delete"),
    path("<int:pk>/return/", views.return_create, name="return_create"),
    path("returns/", views.return_list, name="returns"),
    path("returns/data/", views.return_data, name="return_data"),
]
