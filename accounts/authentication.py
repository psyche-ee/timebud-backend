"""Bearer-token authentication compatible with Laravel Sanctum tokens.

The existing Laravel backend stores in ``personal_access_tokens`` the SHA-256
hash of the random part of a token, and hands the client ``{id}|{random}``.
This class accepts exactly that format, so tokens issued by either backend are
interchangeable and logout only revokes the presented token.
"""

import hashlib
import hmac

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import PersonalAccessToken, User

TOKENABLE_USER_TYPES = {
    "App\\Models\\User",
    "app\\models\\user",
    "accounts.User",
    "accounts.user",
    "User",
    "user",
}


class SanctumTokenAuthentication(BaseAuthentication):
    """Authenticate ``Authorization: Bearer <id>|<token>`` like Sanctum."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        parts = header.split()
        if not parts or parts[0].lower() != self.keyword.lower():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed()

        token_id, separator, plain_token = parts[1].partition("|")
        if not separator or not token_id or not plain_token:
            raise AuthenticationFailed()

        try:
            token = PersonalAccessToken.objects.select_related().get(pk=int(token_id))
        except (ValueError, TypeError, PersonalAccessToken.DoesNotExist):
            raise AuthenticationFailed()

        expected = hashlib.sha256(plain_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected, token.token):
            raise AuthenticationFailed()

        if token.expires_at is not None and token.expires_at <= timezone.now():
            raise AuthenticationFailed()

        user = self._resolve_user(token)
        if user is None or not user.is_active:
            raise AuthenticationFailed()

        # `request.auth` is the token row, which lets logout delete only it.
        return user, token

    @staticmethod
    def _resolve_user(token):
        if token.tokenable_type not in TOKENABLE_USER_TYPES:
            return None
        try:
            return User.objects.get(pk=token.tokenable_id)
        except User.DoesNotExist:
            return None

    def authenticate_header(self, request):
        return self.keyword
