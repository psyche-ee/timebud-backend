"""API routes, mounted at /api/ by config.urls.

Paths mirror the Laravel ``routes/api.php`` definitions exactly (no trailing
slash for business endpoints), including the ``records/{id}`` DELETE route.
"""

from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from accounts import views as auth_views
from attendance import views as record_views
from . import views

urlpatterns = [
    # Public authentication endpoints
    path("register", auth_views.RegisterView.as_view(), name="api-register"),
    path("login", auth_views.LoginView.as_view(), name="api-login"),
    path(
        "forgot-password",
        auth_views.ForgotPasswordView.as_view(),
        name="api-forgot-password",
    ),
    path(
        "reset-password",
        auth_views.ResetPasswordView.as_view(),
        name="api-reset-password",
    ),
    # Protected authentication endpoints
    path(
        "change-password",
        auth_views.ChangePasswordView.as_view(),
        name="api-change-password",
    ),
    path("logout", auth_views.LogoutView.as_view(), name="api-logout"),
    path("auth/google", auth_views.GoogleLoginView.as_view(), name="api-google-login"),
    path("profile", auth_views.ProfileView.as_view(), name="api-profile"),
    # Protected record / DTR endpoints
    path("dashboard", record_views.DashboardView.as_view(), name="api-dashboard"),
    path("time-in", record_views.TimeInView.as_view(), name="api-time-in"),
    path("time-out", record_views.TimeOutView.as_view(), name="api-time-out"),
    path("break-in", record_views.BreakInView.as_view(), name="api-break-in"),
    path("break-out", record_views.BreakOutView.as_view(), name="api-break-out"),
    path("records", record_views.RecordsView.as_view(), name="api-records"),
    path(
        "records/<int:pk>",
        record_views.DeleteRecordView.as_view(),
        name="api-delete-record",
    ),
    path("earnings", record_views.EarningsView.as_view(), name="api-earnings"),
    path(
        "manual-time-in",
        record_views.ManualTimeInView.as_view(),
        name="api-manual-time-in",
    ),
    path(
        "manual-time-out",
        record_views.ManualTimeOutView.as_view(),
        name="api-manual-time-out",
    ),
    path(
        "manual-entry/parse",
        record_views.ManualEntryParseView.as_view(),
        name="api-manual-entry-parse",
    ),
    path(
        "manual-entry",
        record_views.ManualEntryView.as_view(),
        name="api-manual-entry",
    ),
    # Infrastructure
    path("health/", views.health_check, name="api-health"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
    path(
        "redoc/",
        SpectacularRedocView.as_view(url_name="api-schema"),
        name="api-redoc",
    ),
]
