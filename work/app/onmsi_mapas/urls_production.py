from django.urls import path
from .health import health
from .urls import urlpatterns as application_urlpatterns

urlpatterns = [path("health/", health, name="health")] + application_urlpatterns
