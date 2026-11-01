from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("chat/", views.chat_state, name="chat"),
    path("chat/send/", views.chat_send, name="send"),
]
