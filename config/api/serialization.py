"""Helpers that reproduce Laravel's JSON value representations.

Laravel 12 serializes Eloquent ``decimal:2`` casts as **strings** (``"1.50"``),
``datetime`` casts as ISO-8601 with microseconds and a ``Z`` suffix, and numeric
products/sums (PHP floats) as JSON **numbers**. These helpers keep that exact
mix so the existing React components do not need changes.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
import re

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def decimal_string(value, places=2):
    """Render a decimal like Laravel's ``decimal:2`` cast: ``"12.75"``."""
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def to_number(value):
    """Render a computed amount as a JSON number (PHP float arithmetic)."""
    if value is None:
        return 0.0
    return float(Decimal(value))


def php_round(value, places=2):
    """PHP's ``round()`` — half away from zero, unlike Python's banker's round."""
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def laravel_datetime(value):
    """Serialize a datetime as Laravel's ``Carbon::toJSON()`` does."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_timestamp(value):
    """Mimic ``Carbon::parse($value)->setTimezone('Asia/Manila')``.

    Returns an aware UTC datetime. A missing/empty value resolves to "now",
    exactly as Carbon::parse(null) does in the Laravel controllers.

    Accepts ISO-8601 strings (``2026-06-15T14:30:00``) as well as common
    12-hour formats (``2026-06-15 2:30 PM`` or just ``2:30 PM``).
    """
    if value is None or value == "":
        return timezone.now()

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
        if parsed is None:
            date_only = parse_date(str(value))
            if date_only is not None:
                parsed = datetime.combine(date_only, datetime.min.time())

    # Fallback for 12-hour clock strings like "2026-06-15 2:30 PM" or "2:30 PM".
    if parsed is None and isinstance(value, str):
        twelve_hour = _parse_12_hour(value)
        if twelve_hour is not None:
            parsed = twelve_hour

    if parsed is None:
        raise ValueError("Invalid timestamp")

    if timezone.is_naive(parsed):
        # Laravel interprets offset-less input in the app timezone (Manila).
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_12_hour(value):
    """Best-effort parse of 12-hour clock strings with AM/PM.

    Supports:
      - ``2026-06-15 2:30 PM``
      - ``2026-06-15 2:30pm``
      - ``2:30 PM`` (time only → combined with today's date)
    """
    patterns = [
        # Full datetime: 2026-06-15 2:30 PM
        re.compile(
            r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$", re.IGNORECASE
        ),
        # Time only: 2:30 PM
        re.compile(r"^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$", re.IGNORECASE),
    ]

    for pattern in patterns:
        match = pattern.match(value.strip())
        if not match:
            continue

        groups = match.groups()
        if len(groups) == 4:
            date_str, hour_str, minute_str, meridian = groups
            date_part = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            hour_str, minute_str, meridian = groups
            date_part = timezone.localdate()

        hour = int(hour_str)
        minute = int(minute_str)

        # Convert 12-hour to 24-hour.
        meridian_upper = meridian.upper()
        if meridian_upper == "AM":
            if hour == 12:
                hour = 0
        else:  # PM
            if hour != 12:
                hour += 12

        parsed = datetime.combine(date_part, datetime.min.time().replace(hour=hour, minute=minute))
        return parsed

    return None


def elapsed_microseconds(start, end):
    """Exact microseconds between two aware datetimes (may be negative)."""
    delta = end - start
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def work_hours(time_in, time_out):
    """Hours between two punches, rounded like PHP's ``round(..., 2)``.

    Seconds are preserved. A time-out earlier than time-in is a data error, not
    negative work, so it is clamped to zero. Callers that accept user-supplied
    times should reject the combination before reaching this helper.
    """
    if time_in is None or time_out is None:
        return Decimal("0.00")

    total_microseconds = elapsed_microseconds(time_in, time_out)
    if total_microseconds <= 0:
        return Decimal("0.00")

    minutes = total_microseconds / 60_000_000
    return Decimal(str(php_round(minutes / 60, 2)))
