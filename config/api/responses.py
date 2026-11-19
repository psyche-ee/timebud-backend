"""Response helpers that reproduce the Laravel API envelope.

Laravel returns ``{"status": 1|0, "message": "...", "data": ...}`` and omits
the ``data`` key entirely in several endpoints. Use ``OMITTED`` to do the same.
"""

from rest_framework.response import Response

OMITTED = object()


def success_response(data=OMITTED, message="", http_status=200):
    """Build a successful `{status: 1, ...}` response."""
    payload = {"status": 1, "message": message}
    if data is not OMITTED:
        payload["data"] = data
    return Response(payload, status=http_status)


def error_response(message, data=OMITTED, http_status=200):
    """Build a failed `{status: 0, ...}` response.

    Domain errors intentionally use HTTP 200 to match the existing frontend,
    which checks the `status` field rather than the HTTP code.
    """
    payload = {"status": 0, "message": message}
    if data is not OMITTED:
        payload["data"] = data
    return Response(payload, status=http_status)
