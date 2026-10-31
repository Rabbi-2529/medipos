from django.http import HttpResponse
from django.urls import path

from .models import Widget


def widget_names(request):
    return HttpResponse(",".join(Widget.objects.values_list("name", flat=True)))


urlpatterns = [path("widgets/", widget_names)]
