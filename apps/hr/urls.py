from django.urls import path

from . import views

app_name = "hr"

urlpatterns = [
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/calendar/", views.attendance_calendar, name="attendance_calendar"),
    path("attendance/events/", views.attendance_events, name="attendance_events"),
    path("attendance/settings/", views.attendance_settings, name="attendance_settings"),
    path("me/", views.my_attendance, name="my_attendance"),
    path("me/check-in/", views.self_check, {"action": "in"}, name="check_in"),
    path("me/check-out/", views.self_check, {"action": "out"}, name="check_out"),
    path("advances/", views.advances, name="advances"),
    path("advances/<int:pk>/delete/", views.advance_delete, name="advance_delete"),
    path("payroll/", views.payroll_list, name="payroll"),
    path("payroll/<int:pk>/", views.payroll_detail, name="payroll_detail"),
    path("payroll/<int:pk>/pay/", views.payroll_pay, name="payroll_pay"),
    path("payroll/<int:pk>/delete/", views.payroll_delete, name="payroll_delete"),
    path("payroll/<int:pk>/payslip/<int:line_pk>/", views.payslip, name="payslip"),
]
