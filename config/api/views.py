"""Infrastructure endpoints only — no business logic yet."""

from django.db import DatabaseError, connection
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from .responses import error_response, success_response


@extend_schema(
    summary="Service health check",
    description=(
        "Reports service and PostgreSQL connectivity. "
        "Uses the standard TimeBud response envelope."
    ),
    responses={200: dict, 503: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Small smoke-test endpoint used to verify startup and DB connectivity."""
    try:
        connection.ensure_connection()
    except DatabaseError:
        return error_response(message="Database connection failed", http_status=503)

    return success_response(
        data={"status": "ok", "database": "connected"},
        message="Service is healthy",
    )


@extend_schema(
    summary="Simple keep-alive ping",
    description=(
        "Unauthenticated GET endpoint intended for cron jobs. Has no side "
        "effects and is safe to call repeatedly."
    ),
    responses={200: dict},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def ping(request):
    """Return a lightweight success response for scheduled pings."""
    return success_response(
        data={"status": "ok", "timestamp": timezone.now().isoformat()},
        message="pong",
    )
