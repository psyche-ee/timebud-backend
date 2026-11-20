"""Authentication business logic shared by the DRF views.

All password/reset/token behavior follows the existing Laravel controllers:
Sanctum-shaped tokens, bcrypt hashes, one reset token per email, 60-minute
expiry, and a single-use reset flow.
"""

import hashlib
import json
import secrets
import string
import zlib
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .models import PasswordResetToken, PersonalAccessToken

_ALPHANUMERIC = string.ascii_letters + string.digits
_RESET_LINK = "http://localhost:5173/reset-password?token={token}"


def hash_password(raw_password):
    """bcrypt, matching Laravel's default ``bcrypt()`` / ``Hash::make()``."""
    return make_password(raw_password, hasher="bcrypt")


def passwords_match(raw_password, encoded):
    return check_password(raw_password, encoded)


def create_token(user, name):
    """Create a Sanctum-compatible token and return its plain-text value.

    Laravel: ``{id}|{entropy}{crc32b(entropy)}`` is returned to the client,
    while ``sha256({entropy}{crc32b(entropy)})`` is stored in the database.
    """
    entropy = "".join(secrets.choice(_ALPHANUMERIC) for _ in range(40))
    crc32b = f"{zlib.crc32(entropy.encode('utf-8')) & 0xFFFFFFFF:08x}"
    plain_text = f"{entropy}{crc32b}"

    token = PersonalAccessToken.objects.create(
        tokenable_type="App\\Models\\User",
        tokenable_id=user.id,
        name=name,
        token=hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
        abilities=json.dumps(["*"]),
    )
    return f"{token.id}|{plain_text}"


def register_user(validated_data):
    """Create a user exactly as ``AuthController@register`` does."""
    from .models import User

    rate = validated_data.get("rate_per_hr")
    return User.objects.create(
        email=validated_data["email"],
        password=hash_password(validated_data["password"]),
        f_name=validated_data["f_name"],
        l_name=validated_data["l_name"],
        m_name=validated_data.get("m_name"),
        dob=validated_data["dob"],
        gender=validated_data["gender"],
        rate_per_hr=0 if rate is None else rate,
    )


def google_login(credential):
    """Verify a Google ID token and get-or-create the matching user.

    Returns ``(user, token)``. Raises ``ValueError`` when the credential is
    invalid, expired, or issued for a different client.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise ValueError("Google OAuth is not configured on the server.")

    try:
        idinfo = id_token.verify_oauth2_token(
            credential, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise ValueError("Invalid Google token")

    issuer = idinfo.get("iss")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Wrong token issuer")

    if not idinfo.get("email_verified"):
        raise ValueError("Google email is not verified")

    email = idinfo.get("email")
    if not email:
        raise ValueError("Google token has no email")

    from .models import User

    user = User.objects.filter(email=email).first()
    if user is None:
        given_name = idinfo.get("given_name") or ""
        family_name = idinfo.get("family_name") or ""
        user = User(
            email=email,
            f_name=given_name,
            l_name=family_name,
            m_name=None,
            dob=date(2000, 1, 1),  # completed later in Profile
            gender="other",
            rate_per_hr=0,
        )
        user.set_unusable_password()
        user.save()

    token = create_token(user, "TimeBud")
    return user, token


def change_password(user, new_password):
    user.password = hash_password(new_password)
    user.save(update_fields=["password"])


@transaction.atomic
def update_profile(user, validated_data):
    """Persist profile edits and repair records saved before a rate existed.

    Overtime hours/pay are never entered directly: they are derived on
    clock-out from the threshold and rates stored here. A blank/zero overtime
    rate means "use the regular rate for overtime hours too".
    """
    rate = validated_data.get("rate_per_hr")
    ot_rate = validated_data.get("overtime_rate_per_hr")
    for field in (
        "f_name",
        "l_name",
        "m_name",
        "email",
        "dob",
        "gender",
        "rate_per_hr",
        "overtime_threshold_hrs",
        "overtime_rate_per_hr",
    ):
        if field not in validated_data:
            continue
        value = validated_data[field]
        if value is None:
            # A blank overtime input falls back to the regular rate (0 sentinel).
            # A blank threshold restores the 8-hour default.
            value = Decimal("8") if field == "overtime_threshold_hrs" else Decimal("0")
        setattr(user, field, value)
    user.save()

    if rate is not None and rate > 0:
        from attendance.models import Record

        cents = Decimal("0.01")
        effective_ot_rate = ot_rate if ot_rate and ot_rate > 0 else rate
        threshold = user.overtime_threshold_hrs or Decimal("8")
        for record in Record.objects.filter(
            user_id=user.pk, rate_per_hr=0, total_hrs__gt=0
        ):
            total = (record.total_hrs or Decimal("0")).quantize(
                cents, rounding=ROUND_HALF_UP
            )
            regular = (record.regular_hrs or Decimal("0")).quantize(
                cents, rounding=ROUND_HALF_UP
            )
            overtime = (record.overtime_hrs or Decimal("0")).quantize(
                cents, rounding=ROUND_HALF_UP
            )
            # Legacy rows have no split yet — derive it from the threshold.
            if regular == 0 and overtime == 0:
                if total > threshold:
                    regular = threshold
                    overtime = (total - threshold).quantize(
                        cents, rounding=ROUND_HALF_UP
                    )
                else:
                    regular = total

            record.rate_per_hr = rate
            record.overtime_rate_per_hr = effective_ot_rate
            record.regular_hrs = regular
            record.overtime_hrs = overtime
            record.regular_earnings = (regular * rate).quantize(
                cents, rounding=ROUND_HALF_UP
            )
            record.overtime_earnings = (overtime * effective_ot_rate).quantize(
                cents, rounding=ROUND_HALF_UP
            )
            record.earnings = (
                record.regular_earnings + record.overtime_earnings
            ).quantize(cents, rounding=ROUND_HALF_UP)
            record.save(
                update_fields=[
                    "rate_per_hr",
                    "overtime_rate_per_hr",
                    "regular_hrs",
                    "overtime_hrs",
                    "regular_earnings",
                    "overtime_earnings",
                    "earnings",
                ]
            )

    return user


def profile_payload(user):
    """Profile response with Laravel-style decimal strings."""
    from config.api.serialization import decimal_string

    return {
        "user_id": user.id,
        "f_name": user.f_name,
        "l_name": user.l_name,
        "m_name": user.m_name,
        "email": user.email,
        "dob": user.dob.isoformat(),
        "gender": user.gender,
        "rate_per_hr": decimal_string(user.rate_per_hr),
        "overtime_threshold_hrs": decimal_string(user.overtime_threshold_hrs),
        "overtime_rate_per_hr": decimal_string(user.overtime_rate_per_hr),
    }


def send_reset_link(email, token):
    """Send the same subject/link Laravel's ResetPasswordMail sends."""
    link = _RESET_LINK.format(token=token)
    send_mail(
        subject="Reset Your Password",
        message=f"Click the link below to reset your password:\n{link}",
        from_email=None,
        recipient_list=[email],
        html_message=(
            '<p>Click the button below to reset your password.</p>'
            f'<p><a href="{link}">Reset Password</a></p>'
        ),
        fail_silently=False,
    )


@transaction.atomic
def create_reset_token(email):
    """Upsert the one reset token row for an email (email is the PK)."""
    token = "".join(secrets.choice(_ALPHANUMERIC) for _ in range(64))
    PasswordResetToken.objects.update_or_create(
        email=email,
        defaults={"token": token, "created_at": timezone.now()},
    )
    return token


@transaction.atomic
def consume_reset_token(token, new_password):
    """Return (status, message) for the Laravel reset-password flow.

    Statuses: ``ok``, ``invalid``, ``expired``, ``user_missing``.
    """
    record = PasswordResetToken.objects.filter(token=token).first()
    if record is None:
        return "invalid", "Invalid reset token"

    if record.created_at + timedelta(minutes=60) < timezone.now():
        PasswordResetToken.objects.filter(email=record.email).delete()
        return "expired", "Reset link has expired. Please request a new one."

    from .models import User

    user = User.objects.filter(email=record.email).first()
    if user is None:
        return "user_missing", "User not found"

    change_password(user, new_password)
    PasswordResetToken.objects.filter(email=record.email).delete()
    return "ok", "Password successfully reset"


def auth_payload(user, token, include_user_id=False):
    """Auth response ``data`` object in Laravel's field order and types."""
    from config.api.serialization import decimal_string

    payload = {
        "token": token,
        "f_name": user.f_name,
        "l_name": user.l_name,
        "m_name": user.m_name,
        "email": user.email,
        "gender": user.gender,
        "dob": user.dob.isoformat(),
        "rate_per_hr": decimal_string(user.rate_per_hr),
    }
    if include_user_id:
        # Login includes it; registration deliberately does not.
        payload = {"token": token, "user_id": user.id, **{
            key: value for key, value in payload.items() if key != "token"
        }}
    return payload
