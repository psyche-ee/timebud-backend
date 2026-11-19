"""Centralized exception handling for the TimeBud API.

Mirrors two Laravel behaviors from the API contract:

* Validation failures → HTTP 200 with
  ``{"status": 0, "message": "Validation Error", "data": ["..."]}``
  where ``data`` is a flat list of message strings.
* Framework auth failures → HTTP 401 with ``{"message": "Unauthenticated."}``.

Business modules should raise ``DomainError`` for the remaining HTTP-200
business rejections (e.g. "Already clocked in").
"""

from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .responses import error_response


class DomainError(APIException):
    """A business-rule rejection rendered as HTTP 200 `{status: 0, ...}`."""

    status_code = status.HTTP_200_OK
    default_detail = "Request could not be completed."
    default_code = "domain_error"


def _flatten_messages(detail, field=None):
    """Convert DRF error detail structures into Laravel-style string lists.

    Serializer messages already contain the Laravel field label (``"The f name
    field is required."``), so they are passed through unchanged. Bare DRF
    messages are prefixed with their field name for readability.
    """
    messages = []

    if isinstance(detail, dict):
        for key, value in detail.items():
            messages.extend(_flatten_messages(value, key))
    elif isinstance(detail, (list, tuple)):
        for item in detail:
            messages.extend(_flatten_messages(item, field))
    else:
        message = str(detail)
        if field and not message.lower().startswith("the "):
            message = f"{field.replace('_', ' ')}: {message}"
        messages.append(message)

    return messages


def api_exception_handler(exc, context):
    """Custom DRF exception handler wired via REST_FRAMEWORK['EXCEPTION_HANDLER']."""
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        return Response(
            {"message": "Unauthenticated."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, DomainError):
        return error_response(message=str(exc.detail))

    response = drf_exception_handler(exc, context)
    if response is None:
        # Not a DRF exception — let Django produce its own 500 response.
        return None

    if isinstance(exc, ValidationError):
        return error_response(
            message="Validation Error",
            data=_flatten_messages(exc.detail),
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            {"message": "You do not have permission to perform this action."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Remaining framework errors (404, 405, 415, ...) keep Laravel's shape.
    detail = response.data
    message = detail.get("detail") if isinstance(detail, dict) else detail
    return Response({"message": str(message)}, status=response.status_code)
