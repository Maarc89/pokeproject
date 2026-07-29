"""Root URL configuration.

https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("pokedex.urls")),
]

if settings.DEBUG:
    # Django's runserver only; a real deployment serves media through the web
    # server. (Nothing uploads media yet -- this is here for when something does.)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
