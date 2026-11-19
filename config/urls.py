"""Root URL configuration.

All API routes are served under the /api prefix to match the Laravel backend.
"""

from django.urls import include, path

urlpatterns = [
    path("api/", include("config.api.urls")),
]
