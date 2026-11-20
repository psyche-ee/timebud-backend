"""Tests for the Laravel-compatible RecordController endpoints."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import services as auth_services
from accounts.models import User
from attendance.models import Record
from config.api.serialization import parse_timestamp

# 2026-06-15 12:00 Manila (Monday) == 04:00 UTC.
FIXED_NOW = datetime(2026, 6, 15, 4, 0, tzinfo=dt_timezone.utc)


def manila_datetime(year, month, day, hour=0, minute=0, second=0):
    """Build an aware UTC datetime from Manila wall-clock values."""
    return timezone.make_aware(
        datetime(year, month, day, hour, minute, second),
        timezone.get_current_timezone(),
    )


class RecordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(
            email="worker@example.com",
            password="unused",
            f_name="Juan",
            l_name="Dela Cruz",
            dob=date(2000, 1, 1),
            gender="male",
            rate_per_hr=Decimal("100.00"),
        )
        token = auth_services.create_token(self.user, "TimeBud")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def _make_record(self, time_in, time_out=None, total_hrs="0.00", user=None):
        record_user = user or self.user
        hours = Decimal(str(total_hrs))
        rate = record_user.rate_per_hr or Decimal("0.00")
        ot_rate = record_user.overtime_rate_per_hr or rate
        threshold = record_user.overtime_threshold_hrs or Decimal("8.00")
        if hours <= threshold:
            regular_hrs = hours
            overtime_hrs = Decimal("0.00")
        else:
            regular_hrs = threshold
            overtime_hrs = hours - threshold
        regular_hrs = regular_hrs.quantize(Decimal("0.01"))
        overtime_hrs = overtime_hrs.quantize(Decimal("0.01"))
        regular_earnings = (regular_hrs * rate).quantize(Decimal("0.01"))
        overtime_earnings = (overtime_hrs * ot_rate).quantize(Decimal("0.01"))
        return Record.objects.create(
            user=record_user,
            time_in=time_in,
            time_out=time_out,
            total_hrs=hours,
            rate_per_hr=rate,
            overtime_rate_per_hr=ot_rate,
            regular_hrs=regular_hrs,
            overtime_hrs=overtime_hrs,
            regular_earnings=regular_earnings,
            overtime_earnings=overtime_earnings,
            earnings=regular_earnings + overtime_earnings,
        )

    # ------------------------------------------------------------------
    # time-in / time-out
    # ------------------------------------------------------------------
    def test_time_in_success_and_duplicate_guard(self):
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post("/api/time-in", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Clocked in successfully")
        self.assertEqual(
            list(response.data["data"].keys()),
            [
                "id",
                "user_id",
                "time_in",
                "time_out",
                "total_hrs",
                "earnings",
                "regular_hrs",
                "overtime_hrs",
                "regular_earnings",
                "overtime_earnings",
                "overtime_rate_per_hr",
                "created_at",
                "updated_at",
            ],
        )
        self.assertEqual(response.data["data"]["user_id"], self.user.id)
        self.assertEqual(response.data["data"]["total_hrs"], "0.00")
        self.assertIsNone(response.data["data"]["time_out"])
        self.assertTrue(response.data["data"]["time_in"].endswith("Z"))
        self.assertIn(".000000Z", response.data["data"]["time_in"])

        duplicate = self.client.post("/api/time-in", {}, format="json")
        self.assertEqual(duplicate.data["status"], 0)
        self.assertEqual(duplicate.data["message"], "Already clocked in")
        self.assertIsNone(duplicate.data["data"])

    def test_time_out_calculates_hours_and_updates_total(self):
        record = self._make_record(time_in=FIXED_NOW - timedelta(hours=2))

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post("/api/time-out", {}, format="json")

        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Clocked out successfully")
        self.assertEqual(response.data["data"]["total_hrs"], "2.00")
        self.assertEqual(response.data["data"]["time_out"].endswith("Z"), True)
        record.refresh_from_db()
        self.assertEqual(record.total_hrs, Decimal("2.00"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_hrs_worked, Decimal("2.00"))

        none_active = self.client.post("/api/time-out", {}, format="json")
        self.assertEqual(none_active.data["status"], 0)
        self.assertEqual(none_active.data["message"], "No active clock-in found")
        self.assertIsNone(none_active.data["data"])

    def test_hours_preserve_seconds_and_round_like_php(self):
        # Carbon 3 diffInMinutes = 90.9833... => /60 = 1.51639 => round => 1.52.
        self._make_record(time_in=FIXED_NOW - timedelta(minutes=90, seconds=59))
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post("/api/time-out", {}, format="json")
        self.assertEqual(response.data["data"]["total_hrs"], "1.52")

        # 59 seconds => 0.01639 hours => round => 0.02.
        self._make_record(time_in=FIXED_NOW - timedelta(seconds=59))
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post("/api/time-out", {}, format="json")
        self.assertEqual(response.data["data"]["total_hrs"], "0.02")

    def test_backdated_time_out_is_rejected(self):
        record = self._make_record(time_in=FIXED_NOW - timedelta(minutes=30))
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW - timedelta(hours=1)):
            response = self.client.post("/api/time-out", {}, format="json")

        self.assertEqual(response.data["status"], 0)
        self.assertEqual(
            response.data["message"], "Time out cannot be earlier than time in"
        )
        record.refresh_from_db()
        self.assertIsNone(record.time_out)
        self.assertEqual(record.total_hrs, Decimal("0.00"))

    def test_live_endpoints_honor_client_timestamp(self):
        timestamp = FIXED_NOW - timedelta(hours=2)
        response = self.client.post(
            "/api/time-in", {"timestamp": timestamp.isoformat()}, format="json"
        )
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(
            response.data["data"]["time_in"], "2026-06-15T02:00:00.000000Z"
        )

        out_timestamp = timestamp + timedelta(hours=1, minutes=30)
        response = self.client.post(
            "/api/time-out", {"timestamp": out_timestamp.isoformat()}, format="json"
        )
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["data"]["total_hrs"], "1.50")
        self.assertEqual(response.data["data"]["earnings"], 150.0)

    def test_invalid_timestamp_returns_domain_error(self):
        response = self.client.post(
            "/api/time-in", {"timestamp": "not-a-date"}, format="json"
        )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "Invalid timestamp")

    # ------------------------------------------------------------------
    # manual entries
    # ------------------------------------------------------------------
    def test_manual_time_in_and_guards(self):
        timestamp = (FIXED_NOW - timedelta(hours=3)).isoformat()
        response = self.client.post(
            "/api/manual-time-in", {"timestamp": timestamp}, format="json"
        )
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Manual time-in recorded")
        self.assertEqual(response.data["data"]["time_in"].startswith("2026-06-15T01:"), True)

        duplicate = self.client.post(
            "/api/manual-time-in", {"timestamp": timestamp}, format="json"
        )
        self.assertEqual(duplicate.data["message"], "Already clocked in")
        self.assertNotIn("data", duplicate.data)

    def test_manual_endpoints_reject_any_future(self):
        # Any future timestamp (even 1 second ahead) is rejected.
        future = (FIXED_NOW + timedelta(seconds=1)).isoformat()
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-time-in", {"timestamp": future}, format="json"
            )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(
            response.data["message"], "Cannot set time-in in the future"
        )
        self.assertNotIn("data", response.data)

        # Manual time-out in the future is also rejected.
        self._make_record(time_in=FIXED_NOW - timedelta(hours=1))
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-time-out", {"timestamp": future}, format="json"
            )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(
            response.data["message"], "Cannot set time-out in the future"
        )

    def test_manual_time_out_before_time_in_is_rejected(self):
        # Clock in at 10:00, try manual time-out at 09:00 (before time-in).
        self.client.post(
            "/api/manual-time-in",
            {"timestamp": manila_datetime(2026, 6, 15, 10).isoformat()},
            format="json",
        )
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-time-out",
                {"timestamp": manila_datetime(2026, 6, 15, 9).isoformat()},
                format="json",
            )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(
            response.data["message"], "Time out cannot be earlier than time in"
        )

    def test_manual_time_in_accepts_past_date(self):
        past = (FIXED_NOW - timedelta(days=5)).isoformat()
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-time-in", {"timestamp": past}, format="json"
            )
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Manual time-in recorded")

    def test_parse_timestamp_accepts_12_hour_format(self):
        from config.api.serialization import parse_timestamp

        # Full datetime with AM/PM.
        parsed = parse_timestamp("2026-06-15 2:30 PM")
        self.assertEqual(parsed.hour, 14)
        self.assertEqual(parsed.minute, 30)

        # Midnight AM.
        parsed = parse_timestamp("2026-06-15 12:00 AM")
        self.assertEqual(parsed.hour, 0)

        # Noon PM.
        parsed = parse_timestamp("2026-06-15 12:00 PM")
        self.assertEqual(parsed.hour, 12)

        # Time-only defaults to today.
        parsed = parse_timestamp("9:00 AM")
        self.assertEqual(parsed.hour, 9)
        self.assertEqual(parsed.date(), timezone.localdate())

    def test_manual_time_out_success(self):
        self._make_record(time_in=FIXED_NOW - timedelta(hours=1))
        response = self.client.post(
            "/api/manual-time-out", {"timestamp": FIXED_NOW.isoformat()}, format="json"
        )
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Manual time-out recorded")
        self.assertEqual(response.data["data"]["total_hrs"], "1.00")
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_hrs_worked, Decimal("1.00"))

    # ------------------------------------------------------------------
    # break in / break out
    # ------------------------------------------------------------------
    def test_break_in_and_out_reduce_net_hours(self):
        # Clock in at 09:00, break 09:30-10:00 (30 min), clock out at 11:00.
        # Gross = 2h, break = 0.5h, net = 1.5h.
        self.client.post(
            "/api/time-in", {"timestamp": manila_datetime(2026, 6, 15, 9).isoformat()}, format="json"
        )

        with patch("attendance.services.timezone.now", return_value=manila_datetime(2026, 6, 15, 9, 30)):
            bi = self.client.post("/api/break-in", {}, format="json")
        self.assertEqual(bi.data["status"], 1)

        with patch("attendance.services.timezone.now", return_value=manila_datetime(2026, 6, 15, 10)):
            bo = self.client.post("/api/break-out", {}, format="json")
        self.assertEqual(bo.data["status"], 1)
        self.assertEqual(bo.data["data"]["duration_hrs"], "0.50")

        with patch("attendance.services.timezone.now", return_value=manila_datetime(2026, 6, 15, 11)):
            to = self.client.post("/api/time-out", {}, format="json")
        self.assertEqual(to.data["status"], 1)
        self.assertEqual(to.data["data"]["total_hrs"], "1.50")
        self.assertEqual(to.data["data"]["earnings"], 150.0)

    def test_break_in_rejects_when_not_clocked_in(self):
        response = self.client.post("/api/break-in", {}, format="json")
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "No active clock-in found")

    def test_break_out_rejects_when_no_active_break(self):
        self.client.post(
            "/api/time-in", {"timestamp": manila_datetime(2026, 6, 15, 9).isoformat()}, format="json"
        )
        response = self.client.post("/api/break-out", {}, format="json")
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "No active break to end")

    def test_cannot_start_second_break_while_one_is_open(self):
        self.client.post(
            "/api/time-in", {"timestamp": manila_datetime(2026, 6, 15, 9).isoformat()}, format="json"
        )
        with patch("attendance.services.timezone.now", return_value=manila_datetime(2026, 6, 15, 9, 30)):
            self.client.post("/api/break-in", {}, format="json")
        with patch("attendance.services.timezone.now", return_value=manila_datetime(2026, 6, 15, 9, 45)):
            second = self.client.post("/api/break-in", {}, format="json")
        self.assertEqual(second.data["status"], 0)
        self.assertEqual(second.data["message"], "Break already in progress")

    # ------------------------------------------------------------------
    # records list / delete
    # ------------------------------------------------------------------
    def test_records_shape_ordering_and_earnings(self):
        self._make_record(
            time_in=manila_datetime(2026, 6, 15, 9),
            time_out=manila_datetime(2026, 6, 15, 10, 30),
            total_hrs="1.50",
        )
        self._make_record(
            time_in=manila_datetime(2026, 6, 14, 8),
            time_out=manila_datetime(2026, 6, 14, 10),
            total_hrs="2.00",
        )

        response = self.client.get("/api/records")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Records retrieved")
        self.assertEqual(len(response.data["data"]), 2)
        self.assertEqual(
            list(response.data["data"][0].keys()),
            [
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
            ],
        )
        self.assertEqual(response.data["data"][0]["total_hrs"], "1.50")
        self.assertIsInstance(response.data["data"][0]["earnings"], float)
        self.assertEqual(response.data["data"][0]["earnings"], 150.0)
        self.assertEqual(response.data["data"][1]["earnings"], 200.0)
        self.assertGreater(
            response.data["data"][0]["time_in"], response.data["data"][1]["time_in"]
        )

    def test_delete_record_success_rules_and_isolation(self):
        closed = self._make_record(
            time_in=FIXED_NOW - timedelta(hours=2),
            time_out=FIXED_NOW - timedelta(hours=1),
            total_hrs="1.00",
        )
        self.user.total_hrs_worked = Decimal("1.00")
        self.user.save(update_fields=["total_hrs_worked"])

        response = self.client.delete(f"/api/records/{closed.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Record deleted successfully")
        self.assertNotIn("data", response.data)
        self.assertFalse(Record.objects.filter(pk=closed.pk).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_hrs_worked, Decimal("0.00"))

        active = self._make_record(time_in=FIXED_NOW)
        active_result = self.client.delete(f"/api/records/{active.pk}")
        self.assertEqual(
            active_result.data["message"], "Cannot delete active record (no time-out)"
        )
        self.assertNotIn("data", active_result.data)

        missing = self.client.delete("/api/records/999999")
        self.assertEqual(missing.data["message"], "Record not found")
        self.assertNotIn("data", missing.data)

        other = User.objects.create(
            email="other@example.com",
            password="unused",
            f_name="Other",
            l_name="User",
            dob=date(1990, 1, 1),
            gender="female",
        )
        other_record = self._make_record(
            time_in=FIXED_NOW - timedelta(hours=2),
            time_out=FIXED_NOW - timedelta(hours=1),
            total_hrs="1.00",
            user=other,
        )
        foreign = self.client.delete(f"/api/records/{other_record.pk}")
        self.assertEqual(foreign.data["message"], "Record not found")
        self.assertTrue(Record.objects.filter(pk=other_record.pk).exists())

    def test_delete_clamps_user_total_at_zero(self):
        closed = self._make_record(
            time_in=FIXED_NOW - timedelta(hours=2),
            time_out=FIXED_NOW - timedelta(hours=1),
            total_hrs="5.00",
        )
        response = self.client.delete(f"/api/records/{closed.pk}")
        self.assertEqual(response.data["status"], 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_hrs_worked, Decimal("0.00"))

    # ------------------------------------------------------------------
    # earnings / dashboard
    # ------------------------------------------------------------------
    def test_earnings_use_snapshotted_rate_and_week_month_windows(self):
        self._make_record(
            time_in=manila_datetime(2026, 6, 15, 10), total_hrs="1.00"
        )
        self._make_record(
            time_in=manila_datetime(2026, 6, 10, 10), total_hrs="2.00"
        )
        self._make_record(
            time_in=manila_datetime(2026, 5, 20, 10), total_hrs="3.00"
        )

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.get("/api/earnings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message"], "Earnings retrieved")
        data = response.data["data"]
        self.assertEqual(
            {key: data[key] for key in (
                "total", "regular", "overtime", "regular_hrs",
                "overtime_hrs", "this_week", "this_month",
            )},
            {
                "total": 600.0,
                "regular": 600.0,
                "overtime": 0.0,
                "regular_hrs": "6.00",
                "overtime_hrs": "0.00",
                "this_week": 100.0,
                "this_month": 300.0,
            },
        )
        self.assertIn("forecast_week", data)
        self.assertIn("forecast_month", data)
        self.assertIn("forecast_basis", data)

        # Changing the current rate must not rewrite historical earnings.
        self.user.rate_per_hr = Decimal("200.00")
        self.user.save(update_fields=["rate_per_hr"])
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            after_rate_change = self.client.get("/api/earnings")
        after = after_rate_change.data["data"]
        self.assertEqual(
            {key: after[key] for key in (
                "total", "regular", "overtime", "regular_hrs",
                "overtime_hrs", "this_week", "this_month",
            )},
            {
                "total": 600.0,
                "regular": 600.0,
                "overtime": 0.0,
                "regular_hrs": "6.00",
                "overtime_hrs": "0.00",
                "this_week": 100.0,
                "this_month": 300.0,
            },
        )

    def test_overtime_hours_and_pay_are_auto_computed(self):
        # 10h shift, 8h threshold → 8h regular + 2h overtime.
        # Overtime rate of 150 applies only to the 2 overtime hours.
        self.user.overtime_threshold_hrs = Decimal("8.00")
        self.user.overtime_rate_per_hr = Decimal("150.00")
        self.user.save(update_fields=["overtime_threshold_hrs", "overtime_rate_per_hr"])

        # The clock must be after the 18:00 Manila time-out (10:00 UTC).
        later_now = FIXED_NOW + timedelta(hours=12)
        self.client.post(
            "/api/time-in",
            {"timestamp": manila_datetime(2026, 6, 15, 8).isoformat()},
            format="json",
        )
        with patch("attendance.services.timezone.now", return_value=later_now):
            response = self.client.post(
                "/api/time-out",
                {"timestamp": manila_datetime(2026, 6, 15, 18).isoformat()},
                format="json",
            )

        self.assertEqual(response.data["status"], 1)
        data = response.data["data"]
        self.assertEqual(data["total_hrs"], "10.00")
        self.assertEqual(data["regular_hrs"], "8.00")
        self.assertEqual(data["overtime_hrs"], "2.00")
        self.assertEqual(data["regular_earnings"], 800.0)
        self.assertEqual(data["overtime_earnings"], 300.0)
        self.assertEqual(data["earnings"], 1100.0)
        self.assertEqual(data["overtime_rate_per_hr"], "150.00")

        with patch("attendance.services.timezone.now", return_value=later_now):
            earnings = self.client.get("/api/earnings")
        self.assertEqual(earnings.data["data"]["regular_hrs"], "8.00")
        self.assertEqual(earnings.data["data"]["overtime_hrs"], "2.00")
        self.assertEqual(earnings.data["data"]["overtime"], 300.0)

    def test_blank_overtime_rate_uses_regular_rate(self):
        # With no overtime rate set, both regular and overtime hours are paid
        # at the regular rate (total = gross hours × regular rate).
        self.user.overtime_threshold_hrs = Decimal("8.00")
        self.user.overtime_rate_per_hr = Decimal("0.00")
        self.user.save(update_fields=["overtime_threshold_hrs", "overtime_rate_per_hr"])

        later_now = FIXED_NOW + timedelta(hours=12)
        self.client.post(
            "/api/time-in",
            {"timestamp": manila_datetime(2026, 6, 15, 8).isoformat()},
            format="json",
        )
        with patch("attendance.services.timezone.now", return_value=later_now):
            response = self.client.post(
                "/api/time-out",
                {"timestamp": manila_datetime(2026, 6, 15, 18).isoformat()},
                format="json",
            )

        data = response.data["data"]
        self.assertEqual(data["overtime_hrs"], "2.00")
        self.assertEqual(data["overtime_rate_per_hr"], "100.00")
        self.assertEqual(data["overtime_earnings"], 200.0)
        self.assertEqual(data["earnings"], 1000.0)

    def test_cross_midnight_shift_is_split_between_days(self):
        # Sun 23:00 → Mon 01:00 Manila: 1h belongs to Sunday, 1h to Monday.
        self._make_record(
            time_in=manila_datetime(2026, 6, 14, 23),
            time_out=manila_datetime(2026, 6, 15, 1),
            total_hrs="2.00",
        )

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            dashboard = self.client.get("/api/dashboard")
            earnings = self.client.get("/api/earnings")

        data = dashboard.data["data"]
        self.assertEqual(data["today_hours"], 1.0)
        self.assertEqual(data["today_income"], 100.0)
        self.assertEqual(data["weekly_hours"], 1.0)
        self.assertEqual(earnings.data["data"]["this_week"], 100.0)
        self.assertEqual(earnings.data["data"]["this_month"], 200.0)
        self.assertEqual(earnings.data["data"]["total"], 200.0)

    def test_open_record_from_previous_day_reports_clocked_in(self):
        active = self._make_record(time_in=manila_datetime(2026, 6, 14, 23))

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.get("/api/dashboard")

        data = response.data["data"]
        self.assertTrue(data["clocked_in"])
        self.assertEqual(data["today_activity"]["id"], active.pk)
        self.assertIsNone(data["today_activity"]["time_out"])

    def test_week_window_is_half_open(self):
        # A session straddling Sunday midnight is split at the week boundary.
        self._make_record(
            time_in=manila_datetime(2026, 6, 14, 23, 59, 30),
            time_out=manila_datetime(2026, 6, 15, 0, 0, 30),
            total_hrs="0.02",
        )

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            earnings = self.client.get("/api/earnings")

        # Half of the one-minute session (₱1) falls inside Monday.
        self.assertEqual(earnings.data["data"]["this_week"], 1.0)

    def test_dashboard_payload(self):
        today_first = self._make_record(
            time_in=manila_datetime(2026, 6, 15, 9),
            time_out=manila_datetime(2026, 6, 15, 10),
            total_hrs="1.00",
        )
        today_second = self._make_record(
            time_in=manila_datetime(2026, 6, 15, 11),
            time_out=manila_datetime(2026, 6, 15, 12),
            total_hrs="2.00",
        )
        three_days_ago = self._make_record(
            time_in=manila_datetime(2026, 6, 12, 9), total_hrs="1.00"
        )
        self._make_record(time_in=manila_datetime(2026, 6, 1, 9), total_hrs="5.00")

        # Force a deterministic created_at ordering for today_activity.
        Record.objects.filter(pk=today_first.pk).update(
            created_at=FIXED_NOW - timedelta(minutes=10)
        )
        Record.objects.filter(pk=today_second.pk).update(
            created_at=FIXED_NOW - timedelta(minutes=5)
        )

        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message"], "Dashboard data retrieved")
        data = response.data["data"]
        self.assertEqual(data["weekly_hours"], 3.0)
        self.assertEqual(data["weekly_earnings"], 300.0)
        self.assertEqual(data["today_hours"], 3.0)
        self.assertEqual(data["today_income"], 300.0)
        self.assertFalse(data["clocked_in"])
        self.assertIsNone(data["active_time_in"])
        self.assertEqual(data["today_activity"]["id"], today_second.pk)
        self.assertIn("user_id", data["today_activity"])
        self.assertEqual(data["today_activity"]["total_hrs"], "2.00")
        self.assertEqual(data["today_activity"]["earnings"], 200.0)
        self.assertEqual(
            [row["id"] for row in data["past_three_days_records"]],
            [today_second.pk, today_first.pk, three_days_ago.pk],
        )

    def test_records_are_scoped_to_owner(self):
        other = User.objects.create(
            email="other@example.com",
            password="unused",
            f_name="Other",
            l_name="User",
            dob=date(1990, 1, 1),
            gender="female",
            rate_per_hr=Decimal("1000.00"),
        )
        self._make_record(
            time_in=FIXED_NOW - timedelta(hours=1), total_hrs="5.00", user=other
        )

        records = self.client.get("/api/records")
        earnings = self.client.get("/api/earnings")
        dashboard = self.client.get("/api/dashboard")

        self.assertEqual(records.data["data"], [])
        data = earnings.data["data"]
        self.assertEqual(
            {key: data[key] for key in (
                "total", "regular", "overtime", "regular_hrs",
                "overtime_hrs", "this_week", "this_month",
            )},
            {
                "total": 0.0,
                "regular": 0.0,
                "overtime": 0.0,
                "regular_hrs": "0.00",
                "overtime_hrs": "0.00",
                "this_week": 0.0,
                "this_month": 0.0,
            },
        )
        self.assertIn("forecast_week", data)
        self.assertIn("forecast_month", data)
        self.assertEqual(dashboard.data["data"]["weekly_hours"], 0.0)
        self.assertEqual(dashboard.data["data"]["past_three_days_records"], [])

    def test_all_protected_endpoints_require_authentication(self):
        self.client.credentials()
        cases = [
            ("post", "/api/time-in"),
            ("post", "/api/time-out"),
            ("post", "/api/manual-time-in"),
            ("post", "/api/manual-time-out"),
            ("get", "/api/records"),
            ("delete", "/api/records/1"),
            ("get", "/api/earnings"),
            ("get", "/api/dashboard"),
            ("post", "/api/change-password"),
            ("post", "/api/logout"),
        ]
        for method, url in cases:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data, {"message": "Unauthenticated."})
