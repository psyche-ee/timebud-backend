"""Natural-language entry, anomaly detection and earnings forecasting.

Kept separate from the transactional write paths in ``services.py`` so the
read-only "what did you mean / what might be wrong / what will you earn"
logic can be reused by views without circular imports.
"""

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core import signing
from django.utils import timezone

from config.api.serialization import decimal_string, to_number

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
_RANGE_RE = re.compile(
    rf"(?:from\s*)?{_TIME}\s*(?:[-–—]|to|until|till|thru)\s*{_TIME}",
    re.IGNORECASE,
)
_PUNCH_TIME_PATTERNS = [
    re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<mer>a\.?m\.?|p\.?m\.?)?", re.I),
    re.compile(r"(?P<h>\d{1,2})\s*(?P<mer>a\.?m\.?|p\.?m\.?)", re.I),
]
_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.IGNORECASE
)

CONFIRM_SALT = "timebud-anomaly"


# ---------------------------------------------------------------------------
# Natural-language manual entry
# ---------------------------------------------------------------------------


def _meridiem(raw):
    if not raw:
        return None
    cleaned = raw.lower().replace(".", "").replace(" ", "")
    return "am" if cleaned.startswith("a") else "pm"


def _hour24(hour, minute, meridiem):
    hour, minute = int(hour), int(minute or 0)
    if meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem == "pm" and hour != 12:
        hour += 12
    return hour, minute


def _parse_day(text, today):
    """Resolve a relative or explicit day phrase against ``today``."""
    lower = text.lower()

    if re.search(r"\byesterday\b", lower):
        return today - timedelta(days=1)
    if re.search(r"\btoday\b", lower):
        return today

    for name, index in WEEKDAYS.items():
        match = re.search(rf"\b(last|this|on)?\s*{name}\b", lower)
        if not match:
            continue
        prefix = (match.group(1) or "").lower()
        back = (today.weekday() - index) % 7
        if prefix == "last" and back == 0:
            back = 7
        return today - timedelta(days=back)

    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lower)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    for name, number in MONTHS.items():
        match = re.search(
            rf"\b{name}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b",
            lower,
        )
        if match:
            try:
                return date(
                    int(match.group(2) or today.year), number, int(match.group(1))
                )
            except ValueError:
                pass

    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", lower)
    if match:
        month, day_num = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day_num)
        except ValueError:
            pass

    return today


def _parse_break_minutes(text):
    """Return break minutes mentioned near the word 'break', or 0."""
    lower = text.lower()
    if re.search(r"\bno\s+break\b", lower):
        return 0

    match = re.search(r"\bbreak\b", lower)
    if not match:
        return 0

    start = max(0, match.start() - 40)
    window = lower[start : match.end() + 25]
    minutes = 0
    found = False
    for quantity, unit in _UNIT_RE.findall(window):
        value = float(quantity)
        if unit.lower().startswith("h"):
            minutes += value * 60
        else:
            minutes += value
        found = True
    return int(minutes) if found else 0


def _aware(day, hour, minute):
    naive = datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def parse_manual_entry(text):
    """Parse phrases like "worked 8:30 AM-6:15 PM last Tuesday, 30m break".

    Returns ``None`` when no valid time range can be understood. No database
    writes happen here — the caller previews and then saves explicitly.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    text = text.strip()
    range_match = _RANGE_RE.search(text)
    if not range_match:
        return None

    groups = range_match.groups()
    h1, m1, mer1_raw = groups[0], groups[1], groups[2]
    h2, m2, mer2_raw = groups[3], groups[4], groups[5]

    mer1 = _meridiem(mer1_raw)
    mer2 = _meridiem(mer2_raw)
    if mer2 is None:
        # Infer the second meridiem: a descending clock hour means a rollover.
        mer2 = mer1 or "am"
        if int(h2) < int(h1) or (mer1 == "am" and int(h2) <= 11):
            mer2 = "pm" if mer1 == "am" else "am"

    hour1, minute1 = _hour24(h1, m1, mer1 or "am")
    hour2, minute2 = _hour24(h2, m2, mer2)
    if hour1 > 23 or hour2 > 23 or minute1 > 59 or minute2 > 59:
        return None

    today = timezone.localdate(timezone.now())
    day = _parse_day(text, today)

    time_in = _aware(day, hour1, minute1)
    time_out = _aware(day, hour2, minute2)
    if time_out <= time_in:
        time_out += timedelta(days=1)  # overnight shift

    break_minutes = _parse_break_minutes(text)

    return {
        "date": day.isoformat(),
        "time_in": time_in.isoformat(),
        "time_out": time_out.isoformat(),
        "break_minutes": break_minutes,
        "time_in_label": _twelve_label(time_in),
        "time_out_label": _twelve_label(time_out),
        "interpreted": (
            f"{_day_label(day)}, {_twelve_label(time_in)} to "
            f"{_twelve_label(time_out)}, {break_minutes}m break"
        ),
    }


def _twelve_label(value):
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def _day_label(day):
    return f"{day:%a, %b} {day.day}"


def parse_manual_punch(text):
    """Parse a single time such as "8:30 AM last Tuesday".

    Backs the manual time-in/out fields so users can dictate or type a time
    instead of picking a date and time from separate controls.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    match = None
    for pattern in _PUNCH_TIME_PATTERNS:
        match = pattern.search(text)
        if match:
            break
    if match is None:
        return None

    hour = match.group("h")
    minute = match.group("m")
    meridiem = _meridiem(match.group("mer")) or ("am" if int(hour) < 12 else "pm")
    hour24, minute_value = _hour24(hour, minute, meridiem)
    if hour24 > 23 or minute_value > 59:
        return None

    today = timezone.localdate(timezone.now())
    day = _parse_day(text, today)
    moment = _aware(day, hour24, minute_value)

    return {
        "date": day.isoformat(),
        "timestamp": moment.isoformat(),
        "label": _twelve_label(moment),
        "interpreted": f"{_day_label(day)} at {_twelve_label(moment)}",
    }


def compute_preview(user, time_in, time_out, break_minutes):
    """Compute the hours/pay a confirmed session would produce."""
    from . import services

    gross = services.work_hours(time_in, time_out)
    break_hours = services._money(Decimal(str(break_minutes)) / Decimal(60))
    net = services._money(gross - break_hours)
    if net < services.ZERO:
        net = services.ZERO

    rate = services._rate(user)
    ot_rate = services._overtime_rate(user)
    threshold = services._money(user.overtime_threshold_hrs)
    if net <= threshold:
        regular_hrs, overtime_hrs = net, services.ZERO
    else:
        regular_hrs, overtime_hrs = threshold, services._money(net - threshold)

    regular_earnings = services._money(regular_hrs * rate)
    overtime_earnings = services._money(overtime_hrs * ot_rate)

    return {
        "gross_hrs": decimal_string(services._money(gross)),
        "net_hrs": decimal_string(net),
        "break_hrs": decimal_string(break_hours),
        "regular_hrs": decimal_string(regular_hrs),
        "overtime_hrs": decimal_string(overtime_hrs),
        "regular_earnings": to_number(regular_earnings),
        "overtime_earnings": to_number(overtime_earnings),
        "total_earnings": to_number(services._money(regular_earnings + overtime_earnings)),
        "rate_per_hr": decimal_string(rate),
        "overtime_rate_per_hr": decimal_string(ot_rate),
        "threshold_hrs": decimal_string(threshold),
    }


# ---------------------------------------------------------------------------
# Anomaly detection & confirmation tokens
# ---------------------------------------------------------------------------


def detect_anomalies(
    user,
    *,
    action,
    timestamp=None,
    time_in=None,
    time_out=None,
    break_minutes=0,
    explicit_timestamp=False,
):
    """Return advisory warnings for a proposed punch or manual session."""
    from .models import Record

    issues = []
    now = timezone.now()
    candidate = timestamp or time_in

    open_record = (
        Record.objects.filter(user_id=user.pk, time_out__isnull=True, total_hrs=0)
        .order_by("-time_in", "-created_at")
        .first()
    )

    # Hard domain errors belong to the service; do not mask them with a warning.
    if action in ("time-in", "manual-time-in") and open_record is not None:
        return []
    if action == "manual-entry" and open_record is not None:
        return []
    if (
        action in ("time-out", "manual-time-out")
        and open_record is not None
        and candidate is not None
        and candidate <= open_record.time_in
    ):
        return []

    latest = Record.objects.filter(user_id=user.pk).order_by("-time_in").first()
    if candidate is not None and latest is not None:
        if candidate < latest.time_in - timedelta(minutes=1):
            issues.append(
                {
                    "code": "retroactive",
                    "severity": "warning",
                    "message": "This entry is older than your most recent record. "
                    "Please confirm it is intentional.",
                }
            )

    if (
        action in ("time-out", "manual-time-out")
        and not explicit_timestamp
        and open_record is not None
        and open_record.time_in < now - timedelta(hours=12)
    ):
        issues.append(
            {
                "code": "forgotten-timeout",
                "severity": "warning",
                "message": "You have a session from "
                f"{timezone.localtime(open_record.time_in):%b %d} that was never "
                "timed out. Please confirm before continuing.",
            }
        )

    if time_in is not None and time_out is not None and time_out > time_in:
        net = compute_preview(user, time_in, time_out, break_minutes)
        net_hours = Decimal(net["net_hrs"])
        if net_hours > Decimal("12"):
            issues.append(
                {
                    "code": "long-shift",
                    "severity": "warning",
                    "message": f"This shift is {net['net_hrs']}h — unusually long. "
                    "Please confirm the times are correct.",
                }
            )
        if net_hours > Decimal("5") and not break_minutes:
            issues.append(
                {
                    "code": "no-break",
                    "severity": "info",
                    "message": "This shift is over 5h with no break recorded.",
                }
            )
        overlapping = (
            Record.objects.filter(
                user_id=user.pk,
                time_out__isnull=False,
                time_in__lt=time_out,
                time_out__gt=time_in,
            )
            .exists()
        )
        if overlapping:
            issues.append(
                {
                    "code": "overlap",
                    "severity": "warning",
                    "message": "This session overlaps an existing record. "
                    "Please confirm it is not a duplicate.",
                }
            )

    return issues


def canonical_payload(payload):
    """Stable string used to bind a confirmation token to a request body."""
    return json.dumps(payload, sort_keys=True, default=str)


def make_confirmation_token(user, action, payload):
    return signing.dumps(
        {"u": user.pk, "a": action, "p": canonical_payload(payload)},
        salt=CONFIRM_SALT,
    )


def confirmation_valid(token, user, action, payload):
    if not token:
        return False
    try:
        data = signing.loads(token, salt=CONFIRM_SALT, max_age=900)
    except signing.BadSignature:
        return False
    return (
        data.get("u") == user.pk
        and data.get("a") == action
        and data.get("p") == canonical_payload(payload)
    )


def guard_anomalies(request, user, action, payload, detector):
    """Return a confirmation error response when warnings need acknowledgement.

    Returns ``None`` when the write may proceed, either because there are no
    warnings or because a valid confirmation token was supplied.
    """
    from config.api.responses import error_response

    issues = detector()
    if not issues:
        return None

    token = make_confirmation_token(user, action, payload)
    if request.data.get("confirm") and confirmation_valid(
        request.data.get("confirm_token"), user, action, payload
    ):
        return None

    return error_response(
        message="Please confirm this entry",
        data={
            "requires_confirmation": True,
            "warnings": issues,
            "confirm_token": token,
        },
    )


# ---------------------------------------------------------------------------
# Predictive earnings
# ---------------------------------------------------------------------------


def forecast_earnings(user, this_week, this_month):
    """Project week/month totals from the last 8 weeks of weekday earnings."""
    from . import services
    from .models import Record

    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    window_start = today - timedelta(days=56)

    rows = (
        Record.objects.filter(
            user_id=user.pk,
            time_in__date__gte=window_start,
            time_in__date__lte=today,
            total_hrs__gt=0,
        )
        .order_by("time_in")
        .values("time_in", "total_hrs", "earnings")
    )

    rate = services._rate(user)
    daily = defaultdict(lambda: Decimal("0"))
    for row in rows:
        day = timezone.localtime(row["time_in"]).date()
        earnings = row.get("earnings")
        if earnings is None:
            earnings = services._money(row["total_hrs"]) * rate
        daily[day] += services._money(earnings)

    by_weekday = defaultdict(list)
    for day, amount in daily.items():
        by_weekday[day.weekday()].append(amount)

    averages = {
        weekday: sum(values) / Decimal(len(values))
        for weekday, values in by_weekday.items()
    }
    if not averages:
        typical = services._money(rate * Decimal("8"))
        averages = {weekday: typical for weekday in range(7)}

    def remaining_total(start_day, end_day):
        total = Decimal("0")
        day = start_day
        while day <= end_day:
            total += averages.get(day.weekday(), Decimal("0"))
            day += timedelta(days=1)
        return services._money(total)

    tomorrow = today + timedelta(days=1)
    week_end = today - timedelta(days=today.weekday()) + timedelta(days=6)
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)

    projected_week = services._money(
        Decimal(str(this_week)) + remaining_total(tomorrow, week_end)
    )
    projected_month = services._money(
        Decimal(str(this_month)) + remaining_total(tomorrow, month_end)
    )

    return {
        "week": to_number(projected_week),
        "month": to_number(projected_month),
        "basis": "Based on your last 8 weeks of earnings",
    }
