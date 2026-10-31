from django.urls import path

from . import views

app_name = "outlets"

urlpatterns = [
    path("", views.OutletListView.as_view(), name="list"),
    path("new/", views.OutletCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.OutletUpdateView.as_view(), name="edit"),
    path("<int:pk>/toggle/", views.OutletToggleView.as_view(), name="toggle"),
    path("<int:pk>/enter/", views.enter_outlet, name="enter"),
    path("counters/", views.CounterListView.as_view(), name="counters"),
    path("counters/new/", views.CounterCreateView.as_view(), name="counter_create"),
    path("counters/<int:pk>/edit/", views.CounterUpdateView.as_view(), name="counter_edit"),
    path("counters/<int:pk>/toggle/", views.CounterToggleView.as_view(), name="counter_toggle"),
]
