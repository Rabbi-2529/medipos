from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("employees/", views.employee_list, name="employees"),
    path("employees/data/", views.employee_data, name="employee_data"),
    path("employees/new/", views.employee_create, name="employee_create"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/toggle/", views.employee_toggle, name="employee_toggle"),
    path("employees/<int:pk>/password/", views.employee_password, name="employee_password"),
    path("roles/", views.role_list, name="roles"),
    path("roles/new/", views.role_create, name="role_create"),
    path("roles/<int:pk>/edit/", views.role_edit, name="role_edit"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
]
