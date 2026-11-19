"""Tests for the Laravel-compatible RecordController endpoints."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import services as auth_services
from accounts.models import User
from attendance.models import Record

# 2026-06-15 12:00 Manila (Monday) == 04:00 UTC.
FIXED_NOW = datetime(2026, 6, 15, 4, 0, tzinfo=dt_timezone.utc)


def manila_datetime(year, month, day, hour=0, minute=0):
    """Build an aware UTC datetime from Manila wall-clock values."""
    return timezone.make_aware(
        datetime(year, month, day, hour, minute), timezone.get_current_timezone()
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
        return Record.objects.create(
            user=user or self.user,
            time_in=time_in,
            time_out=time_out,
            total_hrs=Decimal(str(total_hrs)),
        )

    # ------------------------------------------------------------------
    # time-in / time-out
    # ------------------------------------------------------------------
    def test_time_in_success_and_duplicate_guard(self):
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post("/api/time-in", {"timestamp": "ignored"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Clocked in successfully")
        self.assertEqual(
            list(response.data["data"].keys()),
            ["id", "user_id", "time_in", "time_out", "total_hrs", "created_at", "updated_at"],
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

    def test_hours_are_signed_when_time_out_precedes_time_in(self):
        # Carbon 3 diffInMinutes is signed; a backdated time-out yields negatives.
        self._make_record(time_in=FIXED_NOW - timedelta(minutes=30))
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW - timedelta(hours=1)):
            response = self.client.post("/api/time-out", {}, format="json")
        self.assertEqual(response.data["data"]["total_hrs"], "-0.50")

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

    def test_manual_endpoints_reject_too_far_future(self):
        future = (FIXED_NOW + timedelta(days=2)).isoformat()
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-time-in", {"timestamp": future}, format="json"
            )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "Too far in the future")
        self.assertNotIn("data", response.data)

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
            ["id", "time_in", "time_out", "total_hrs", "earnings"],
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
    def test_earnings_use_current_rate_and_week_month_windows(self):
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
        self.assertEqual(
            response.data["data"], {"total": 600.0, "this_week": 100.0, "this_month": 300.0}
        )

        self.user.rate_per_hr = Decimal("200.00")
        self.user.save(update_fields=["rate_per_hr"])
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            retroactive = self.client.get("/api/earnings")
        self.assertEqual(
            retroactive.data["data"],
            {"total": 1200.0, "this_week": 200.0, "this_month": 600.0},
        )

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
        self.assertEqual(data["today_activity"]["id"], today_second.pk)
        self.assertIn("user_id", data["today_activity"])
        self.assertEqual(data["today_activity"]["total_hrs"], "2.00")
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
        self.assertEqual(
            earnings.data["data"], {"total": 0.0, "this_week": 0.0, "this_month": 0.0}
        )
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
