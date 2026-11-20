"""Attendance business rules shared by all record endpoints.

Write paths are transactional and lock the user row so the denormalized
``total_hrs_worked`` cannot lose updates. Earnings are snapshotted onto each
record when a session closes; historical income never changes when the user's
current rate changes.

Worked (net) hours = gross elapsed (time_out − time_in) minus break time.
Overtime is computed per shift against the user's daily threshold; hours above
the threshold are paid at the user's overtime rate (falling back to the
regular rate when the overtime rate is zero).
"""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from config.api.serialization import (
    decimal_string,
    elapsed_microseconds,
    laravel_datetime,
    parse_timestamp,
    to_number,
    work_hours,
)

from .models import Break, Record

User = get_user_model()

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def _decimal(value):
    return Decimal(value) if value is not None else ZERO


def _money(value):
    return _decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def _rate(user):
    return _money(user.rate_per_hr)


def _overtime_rate(user):
    ot = _money(user.overtime_rate_per_hr)
    return ot if ot > ZERO else _rate(user)


def _week_start(now_local):
    return (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _month_start(now_local):
    return now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(month_start):
    return (month_start + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _break_microseconds(record):
    """Total closed break microseconds for a record."""
    total = 0
    for br in record.breaks.filter(time_out__isnull=False):
        total += elapsed_microseconds(br.time_in, br.time_out)
    return total


def _overlap_rows(user, start, end):
    """Closed sessions whose worked interval overlaps [start, end)."""
    return list(
        Record.objects.filter(
            user_id=user.pk,
            total_hrs__gt=0,
            time_in__lt=end,
        ).filter(
            Q(time_out__gt=start) | Q(time_out__isnull=True)
        ).values(
            "time_in",
            "time_out",
            "total_hrs",
            "earnings",
            "regular_earnings",
            "overtime_earnings",
            "rate_per_hr",
        )
    )


def _allocate(rows, start, end, current_rate):
    """Split session hours/earnings across a time window.

    A shift that crosses midnight contributes to both days proportionally to
    the time actually worked inside each window. The stored, rounded
    ``total_hrs`` and its snapshotted earnings remain authoritative.
    """
    total_hours = ZERO
    total_earnings = ZERO

    for row in rows:
        row_hours = _money(row.get("total_hrs"))
        if row_hours <= ZERO:
            continue

        time_in = row.get("time_in")
        if time_in is None:
            continue

        time_out = row.get("time_out")
        if time_out is None:
            # Legacy rows may carry total_hrs without an explicit time_out.
            derived_seconds = int(row_hours * Decimal("3600"))
            time_out = time_in + timedelta(seconds=derived_seconds)

        duration = elapsed_microseconds(time_in, time_out)
        if duration <= 0:
            continue

        overlap = elapsed_microseconds(max(time_in, start), min(time_out, end))
        if overlap <= 0:
            continue

        share = Decimal(overlap) / Decimal(duration)
        total_hours += row_hours * share

        row_earnings = row.get("earnings")
        if row_earnings is None:
            row_earnings = row_hours * (
                _money(row.get("rate_per_hr")) or current_rate
            )
        total_earnings += _money(row_earnings) * share

    return _money(total_hours), _money(total_earnings)


def _latest_record(user, open_only=False):
    """Latest open record by punch time, falling back to creation time."""
    queryset = Record.objects.filter(user_id=user.pk)
    if open_only:
        queryset = queryset.filter(time_out__isnull=True, total_hrs=0)
    return queryset.order_by("-time_in", "-created_at").first()


def _latest_open_break(record):
    """The break on ``record`` that has not been closed yet, if any."""
    return (
        record.breaks.filter(time_out__isnull=True).order_by("-time_in").first()
    )


def _parse_timestamp(raw_timestamp):
    try:
        return parse_timestamp(raw_timestamp), None
    except (TypeError, ValueError):
        return None, "Invalid timestamp"


def _is_future(timestamp):
    """Return True if ``timestamp`` is strictly after the current moment."""
    return timestamp > timezone.now()


def _recompute_user_total(user):
    aggregate = Record.objects.filter(user_id=user.pk).aggregate(
        total=Coalesce(Sum("total_hrs"), Value(ZERO))
    )
    user.total_hrs_worked = _money(aggregate["total"])
    user.save(update_fields=["total_hrs_worked"])


def _close_record(user, record, timestamp):
    """Close a record, subtract breaks, split regular/overtime, snapshot rates."""
    if timestamp < record.time_in:
        return "Time out cannot be earlier than time in"
    if _is_future(timestamp):
        return "Cannot set time-out in the future"

    gross_hours = work_hours(record.time_in, timestamp)
    break_us = _break_microseconds(record)
    break_hours = _money(Decimal(break_us) / Decimal(3_600_000_000))
    net_hours = _money(gross_hours - break_hours)
    if net_hours < ZERO:
        net_hours = ZERO

    rate = _rate(user)
    ot_rate = _overtime_rate(user)
    threshold = _money(user.overtime_threshold_hrs)

    if net_hours <= threshold:
        regular_hrs = net_hours
        overtime_hrs = ZERO
    else:
        regular_hrs = threshold
        overtime_hrs = _money(net_hours - threshold)

    regular_earnings = _money(regular_hrs * rate)
    overtime_earnings = _money(overtime_hrs * ot_rate)
    total_earnings = _money(regular_earnings + overtime_earnings)

    record.time_out = timestamp
    record.total_hrs = net_hours
    record.rate_per_hr = rate
    record.overtime_rate_per_hr = ot_rate
    record.regular_hrs = regular_hrs
    record.overtime_hrs = overtime_hrs
    record.regular_earnings = regular_earnings
    record.overtime_earnings = overtime_earnings
    record.earnings = total_earnings
    record.save(
        update_fields=[
            "time_out",
            "total_hrs",
            "rate_per_hr",
            "overtime_rate_per_hr",
            "regular_hrs",
            "overtime_hrs",
            "regular_earnings",
            "overtime_earnings",
            "earnings",
            "updated_at",
        ]
    )
    _recompute_user_total(user)
    return None


@transaction.atomic
def clock_in(user, raw_timestamp=None):
    """Live time-in. Returns (record, error_message)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error
    if _is_future(timestamp):
        return None, "Too far in the future"

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    if _latest_record(locked_user, open_only=True) is not None:
        return None, "Already clocked in"

    record = Record.objects.create(
        user_id=locked_user.pk,
        time_in=timestamp,
        total_hrs=ZERO,
        rate_per_hr=ZERO,
        earnings=ZERO,
    )
    return record, None


@transaction.atomic
def clock_out(user, raw_timestamp=None):
    """Live time-out. Returns (record, error_message)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    error = _close_record(locked_user, record, timestamp)
    return (None, error) if error else (record, None)


@transaction.atomic
def manual_time_in(user, raw_timestamp):
    """Manual time-in. Returns (record, error_message)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error
    if _is_future(timestamp):
        return None, "Cannot set time-in in the future"

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    if _latest_record(locked_user, open_only=True) is not None:
        return None, "Already clocked in"

    record = Record.objects.create(
        user_id=locked_user.pk,
        time_in=timestamp,
        total_hrs=ZERO,
        rate_per_hr=ZERO,
        earnings=ZERO,
    )
    return record, None


@transaction.atomic
def manual_time_out(user, raw_timestamp):
    """Manual time-out. Returns (record, error_message)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    error = _close_record(locked_user, record, timestamp)
    return (None, error) if error else (record, None)


@transaction.atomic
def create_manual_session(user, raw_time_in, raw_time_out, break_minutes=0):
    """Create a complete past session (including its break) in one write.

    Used by the natural-language entry flow so a described shift becomes a
    single, consistent record rather than three loosely coupled punches.
    Returns (record, error_message).
    """
    time_in, error = _parse_timestamp(raw_time_in)
    if error:
        return None, error
    time_out, error = _parse_timestamp(raw_time_out)
    if error:
        return None, error

    if _is_future(time_in) or _is_future(time_out):
        return None, "Cannot save a session in the future"
    if time_out <= time_in:
        return None, "Time out must be after time in"

    try:
        minutes = max(0, int(break_minutes or 0))
    except (TypeError, ValueError):
        return None, "Invalid break duration"

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    if _latest_record(locked_user, open_only=True) is not None:
        return None, "Already clocked in"

    record = Record.objects.create(
        user_id=locked_user.pk,
        time_in=time_in,
        total_hrs=ZERO,
        rate_per_hr=ZERO,
        earnings=ZERO,
    )

    if minutes > 0:
        duration = time_out - time_in
        break_delta = timedelta(minutes=minutes)
        if break_delta >= duration:
            return None, "Break cannot be longer than the shift"
        # Centre the break in the shift so it always lies inside the punches.
        break_start = time_in + (duration - break_delta) / 2
        Break.objects.create(
            record_id=record.pk,
            time_in=break_start,
            time_out=break_start + break_delta,
            duration_hrs=_money(Decimal(minutes) / Decimal(60)),
        )

    error = _close_record(locked_user, record, time_out)
    return (None, error) if error else (record, None)


@transaction.atomic
def start_break(user, raw_timestamp=None):
    """Start a break on the user's open record. Returns (break, error)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error
    if _is_future(timestamp):
        return None, "Cannot set break-in in the future"

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    open_break = _latest_open_break(record)
    if open_break is not None:
        return None, "Break already in progress"

    break_row = Break.objects.create(
        record_id=record.pk,
        time_in=timestamp,
        duration_hrs=ZERO,
    )
    return break_row, None


@transaction.atomic
def end_break(user, raw_timestamp=None):
    """End the user's current break. Returns (break, error)."""
    timestamp, error = _parse_timestamp(raw_timestamp)
    if error:
        return None, error

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    break_row = _latest_open_break(record)
    if break_row is None:
        return None, "No active break to end"

    if timestamp < break_row.time_in:
        return None, "Break time out cannot be earlier than time in"
    if _is_future(timestamp):
        return None, "Cannot set break-out in the future"

    duration_us = elapsed_microseconds(break_row.time_in, timestamp)
    break_row.time_out = timestamp
    break_row.duration_hrs = _money(
        Decimal(duration_us) / Decimal(3_600_000_000)
    )
    break_row.save(update_fields=["time_out", "duration_hrs", "updated_at"])
    return break_row, None


@transaction.atomic
def delete_record(user, record_id):
    """Delete one of the user's closed records. Returns an error message or None."""
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = Record.objects.filter(pk=record_id, user_id=locked_user.pk).first()
    if record is None:
        return "Record not found"
    if record.time_out is None:
        return "Cannot delete active record (no time-out)"

    record.delete()
    _recompute_user_total(locked_user)
    return None


def get_records(user):
    """Records ordered by ``time_in DESC`` with the full map keys."""
    rate = _rate(user)
    rows = (
        Record.objects.filter(user_id=user.pk)
        .order_by("-time_in")
        .values(
            "id",
            "time_in",
            "time_out",
            "total_hrs",
            "earnings",
            "regular_hrs",
            "overtime_hrs",
            "regular_earnings",
            "overtime_earnings",
            "rate_per_hr",
            "overtime_rate_per_hr",
        )
    )

    result = []
    for row in rows:
        earnings = row.get("earnings")
        if earnings is None:
            earnings = _money(row.get("total_hrs")) * (
                _money(row.get("rate_per_hr")) or rate
            )
        result.append(
            {
                "id": row["id"],
                "time_in": laravel_datetime(row["time_in"]),
                "time_out": laravel_datetime(row["time_out"]),
                "total_hrs": decimal_string(row["total_hrs"]),
                "earnings": to_number(_money(earnings)),
                "regular_hrs": decimal_string(row.get("regular_hrs")),
                "overtime_hrs": decimal_string(row.get("overtime_hrs")),
                "regular_earnings": to_number(
                    _money(row.get("regular_earnings"))
                ),
                "overtime_earnings": to_number(
                    _money(row.get("overtime_earnings"))
                ),
                "rate_per_hr": decimal_string(row.get("rate_per_hr")),
                "overtime_rate_per_hr": decimal_string(
                    row.get("overtime_rate_per_hr")
                ),
            }
        )
    return result


def get_earnings(user):
    """All-time / this-week / this-month earnings with regular/overtime split."""
    now_local = timezone.localtime(timezone.now())
    week_start = _week_start(now_local)
    month_start = _month_start(now_local)
    now = timezone.now()
    rate = _rate(user)

    aggregate = Record.objects.filter(user_id=user.pk).aggregate(
        total=Coalesce(Sum("total_hrs"), Value(ZERO)),
        earnings=Coalesce(Sum("earnings"), Value(ZERO)),
        regular=Coalesce(Sum("regular_earnings"), Value(ZERO)),
        overtime=Coalesce(Sum("overtime_earnings"), Value(ZERO)),
        regular_hrs=Coalesce(Sum("regular_hrs"), Value(ZERO)),
        overtime_hrs=Coalesce(Sum("overtime_hrs"), Value(ZERO)),
    )

    _, week_earnings = _allocate(
        _overlap_rows(user, week_start, now), week_start, now, rate
    )
    _, month_earnings = _allocate(
        _overlap_rows(user, month_start, now), month_start, now, rate
    )

    total_earnings = _money(aggregate["earnings"])
    total_regular = _money(aggregate["regular"])
    total_overtime = _money(aggregate["overtime"])
    if total_earnings <= ZERO and aggregate["total"] > ZERO:
        total_earnings = _money(aggregate["total"] * rate)

    from .intelligence import forecast_earnings

    forecast = forecast_earnings(user, week_earnings, month_earnings)

    return {
        "total": to_number(total_earnings),
        "regular": to_number(total_regular),
        "overtime": to_number(total_overtime),
        "regular_hrs": decimal_string(_money(aggregate["regular_hrs"])),
        "overtime_hrs": decimal_string(_money(aggregate["overtime_hrs"])),
        "this_week": to_number(week_earnings),
        "this_month": to_number(month_earnings),
        "forecast_week": forecast["week"],
        "forecast_month": forecast["month"],
        "forecast_basis": forecast["basis"],
    }


def get_dashboard(user):
    """Dashboard aggregates with explicit clock-in state and local boundaries."""
    now = timezone.now()
    now_local = timezone.localtime(now)
    today = now_local.date()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = _week_start(now_local)
    past_start = timezone.make_aware(
        datetime.combine(today - timedelta(days=3), datetime.min.time()),
        timezone.get_current_timezone(),
    )
    rate = _rate(user)

    weekly_hours, weekly_earnings = _allocate(
        _overlap_rows(user, week_start, now), week_start, now, rate
    )
    today_hours, today_income = _allocate(
        _overlap_rows(user, today_start, now), today_start, now, rate
    )

    active = (
        Record.objects.filter(user_id=user.pk, time_out__isnull=True, total_hrs=0)
        .order_by("-time_in", "-created_at")
        .first()
    )
    active_break = None
    session_break_minutes = 0
    if active is not None:
        active_break = _latest_open_break(active)
        for br in active.breaks.filter(time_out__isnull=False):
            session_break_minutes += int(
                elapsed_microseconds(br.time_in, br.time_out)
                / Decimal(60_000_000)
            )

    latest_today = (
        Record.objects.filter(user_id=user.pk, time_in__date=today)
        .order_by("-created_at")
        .first()
    )
    today_activity = active or latest_today

    recent = list(
        Record.objects.filter(
            user_id=user.pk,
            time_in__gte=past_start,
            time_in__lt=now + timedelta(days=1),
        ).order_by("-time_in")[:10]
    )

    from .serializers import RecordSerializer

    return {
        "weekly_hours": to_number(weekly_hours),
        "weekly_earnings": to_number(weekly_earnings),
        "today_hours": to_number(today_hours),
        "today_income": to_number(today_income),
        "clocked_in": active is not None,
        "active_time_in": laravel_datetime(active.time_in) if active else None,
        "overtime_threshold_hrs": decimal_string(_money(user.overtime_threshold_hrs)),
        "overtime_rate_per_hr": decimal_string(_overtime_rate(user)),
        "session_break_minutes": session_break_minutes,
        "active_break_in": (
            laravel_datetime(active_break.time_in) if active_break else None
        ),
        "break_elapsed_minutes": (
            int(
                elapsed_microseconds(active_break.time_in, now)
                / Decimal(60_000_000)
            )
            if active_break
            else 0
        ),
        "today_activity": (
            RecordSerializer(today_activity).data if today_activity else None
        ),
        "past_three_days_records": RecordSerializer(recent, many=True).data,
    }
