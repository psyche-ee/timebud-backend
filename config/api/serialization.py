"""Helpers that reproduce Laravel's JSON value representations.

Laravel 12 serializes Eloquent ``decimal:2`` casts as **strings** (``"1.50"``),
``datetime`` casts as ISO-8601 with microseconds and a ``Z`` suffix, and numeric
products/sums (PHP floats) as JSON **numbers**. These helpers keep that exact
mix so the existing React components do not need changes.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP

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

    if parsed is None:
        raise ValueError("Invalid timestamp")

    if timezone.is_naive(parsed):
        # Laravel interprets offset-less input in the app timezone (Manila).
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def work_hours(time_in, time_out):
    """Laravel: ``round(Carbon::parse(time_in)->diffInMinutes(time_out) / 60, 2)``.

    Carbon 3.11.1 ``diffInMinutes()`` is a **signed, fractional** value derived
    from ``diffInMicroseconds() / 60``. Seconds are therefore preserved, and a
    time-out before time-in produces a negative result. ``php_round`` supplies
    PHP's half-away-from-zero rounding.
    """
    if time_in is None or time_out is None:
        return Decimal("0.00")

    delta = time_out - time_in
    total_microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    minutes = total_microseconds / 60_000_000
    return Decimal(str(php_round(minutes / 60, 2)))
