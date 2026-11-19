"""Attendance business rules shared by all record endpoints.

Centralizing this logic keeps live clock-in/out, manual entries, listings,
earnings and the dashboard consistent. Write paths are transactional and lock
the user row so the denormalized ``total_hrs_worked`` cannot lose updates.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from config.api.serialization import (
    decimal_string,
    laravel_datetime,
    parse_timestamp,
    to_number,
    work_hours,
)

from .models import Record

User = get_user_model()


def _latest_record(user, open_only=False):
    """Laravel's ``latest()`` orders by ``created_at DESC``, not by id."""
    queryset = Record.objects.filter(user_id=user.pk)
    if open_only:
        queryset = queryset.filter(time_out__isnull=True)
    return queryset.order_by("-created_at").first()


@transaction.atomic
def clock_in(user):
    """Live time-in. Returns (record, error_message)."""
    last = _latest_record(user)
    if last is not None and last.time_out is None:
        return None, "Already clocked in"

    record = Record.objects.create(
        user_id=user.pk,
        time_in=timezone.now(),
        total_hrs=Decimal("0.00"),
    )
    return record, None


@transaction.atomic
def clock_out(user):
    """Live time-out. Returns (record, error_message)."""
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    record.time_out = timezone.now()
    record.total_hrs = work_hours(record.time_in, record.time_out)
    record.save(update_fields=["time_out", "total_hrs", "updated_at"])

    locked_user.total_hrs_worked = (
        locked_user.total_hrs_worked or Decimal("0.00")
    ) + record.total_hrs
    locked_user.save(update_fields=["total_hrs_worked"])
    return record, None


@transaction.atomic
def manual_time_in(user, raw_timestamp):
    """Manual time-in. Returns (record, error_message)."""
    timestamp = parse_timestamp(raw_timestamp)

    last = _latest_record(user)
    if last is not None and last.time_out is None:
        return None, "Already clocked in"

    if timestamp > timezone.now() + timedelta(days=1):
        return None, "Too far in the future"

    record = Record.objects.create(
        user_id=user.pk,
        time_in=timestamp,
        total_hrs=Decimal("0.00"),
    )
    return record, None


@transaction.atomic
def manual_time_out(user, raw_timestamp):
    """Manual time-out. Returns (record, error_message)."""
    timestamp = parse_timestamp(raw_timestamp)

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = _latest_record(locked_user, open_only=True)
    if record is None:
        return None, "No active clock-in found"

    if timestamp > timezone.now() + timedelta(days=1):
        return None, "Too far in the future"

    record.time_out = timestamp
    record.total_hrs = work_hours(record.time_in, record.time_out)
    record.save(update_fields=["time_out", "total_hrs", "updated_at"])

    locked_user.total_hrs_worked = (
        locked_user.total_hrs_worked or Decimal("0.00")
    ) + record.total_hrs
    locked_user.save(update_fields=["total_hrs_worked"])
    return record, None


@transaction.atomic
def delete_record(user, record_id):
    """Delete one of the user's closed records. Returns an error message or None."""
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    record = Record.objects.filter(pk=record_id, user_id=locked_user.pk).first()
    if record is None:
        return "Record not found"
    if record.time_out is None:
        return "Cannot delete active record (no time-out)"

    remaining = (locked_user.total_hrs_worked or Decimal("0.00")) - record.total_hrs
    locked_user.total_hrs_worked = max(remaining, Decimal("0.00"))
    locked_user.save(update_fields=["total_hrs_worked"])
    record.delete()
    return None


def get_records(user):
    """Records ordered by ``time_in DESC`` with the five Laravel map keys."""
    rate = user.rate_per_hr or Decimal("0.00")
    records = Record.objects.filter(user_id=user.pk).order_by("-time_in")
    return [
        {
            "id": record.id,
            "time_in": laravel_datetime(record.time_in),
            "time_out": laravel_datetime(record.time_out),
            "total_hrs": decimal_string(record.total_hrs),
            "earnings": to_number(record.total_hrs * rate),
        }
        for record in records
    ]


def get_earnings(user):
    """All-time / this-week / this-month earnings at the *current* rate."""
    rate = user.rate_per_hr or Decimal("0.00")
    records = list(Record.objects.filter(user_id=user.pk))

    now_local = timezone.localtime(timezone.now())
    start_of_week = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_of_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def total_for(rows):
        return to_number(sum((row.total_hrs * rate for row in rows), Decimal("0.00")))

    return {
        "total": total_for(records),
        "this_week": total_for(
            [row for row in records if row.time_in >= start_of_week]
        ),
        "this_month": total_for(
            [row for row in records if row.time_in >= start_of_month]
        ),
    }


def get_dashboard(user):
    """Dashboard aggregates with Laravel's week/day boundaries and ordering."""
    rate = user.rate_per_hr or Decimal("0.00")

    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    start_of_week = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59, seconds=59)

    weekly_records = Record.objects.filter(
        user_id=user.pk, time_in__range=(start_of_week, end_of_week)
    )
    weekly_hours = sum((row.total_hrs for row in weekly_records), Decimal("0.00"))

    today_records = list(Record.objects.filter(user_id=user.pk, time_in__date=today))
    today_hours = sum((row.total_hrs for row in today_records), Decimal("0.00"))

    # `today_activity` uses Laravel's latest() => created_at DESC.
    today_activity = (
        max(today_records, key=lambda row: row.created_at) if today_records else None
    )

    past_three_days = Record.objects.filter(
        user_id=user.pk, time_in__date__gte=today - timedelta(days=3)
    ).order_by("-time_in")

    from .serializers import RecordSerializer

    return {
        "weekly_hours": to_number(weekly_hours),
        "weekly_earnings": to_number(weekly_hours * rate),
        "today_hours": to_number(today_hours),
        "today_income": to_number(today_hours * rate),
        "today_activity": (
            RecordSerializer(today_activity).data if today_activity else None
        ),
        "past_three_days_records": RecordSerializer(past_three_days, many=True).data,
    }
