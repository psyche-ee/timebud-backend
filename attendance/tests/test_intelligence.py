"""Tests for natural-language entry, anomaly detection and forecasting."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import services as auth_services
from accounts.models import User
from attendance.intelligence import parse_manual_entry, parse_manual_punch
from attendance.models import Break, Record

FIXED_NOW = datetime(2026, 6, 15, 4, 0, tzinfo=dt_timezone.utc)  # Mon 12:00 Manila


class IntelligenceTests(APITestCase):
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

    # ------------------------------------------------------------------
    # 1. Natural-language manual entry
    # ------------------------------------------------------------------
    def test_parse_natural_language_with_break(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            parsed = parse_manual_entry(
                "worked 8:30 AM–6:15 PM last Tuesday, 30m break"
            )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["date"], "2026-06-09")  # last Tuesday
        self.assertEqual(parsed["break_minutes"], 30)
        self.assertEqual(parsed["time_in_label"], "8:30 AM")
        self.assertEqual(parsed["time_out_label"], "6:15 PM")
        self.assertIn("08:30", parsed["time_in"])
        self.assertIn("18:15", parsed["time_out"])

    def test_parse_range_when_date_phrase_sits_between_times(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            parsed = parse_manual_entry(
                "worked 8:30am november 18 to 12:40pm november 18"
            )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["date"], "2026-11-18")
        self.assertEqual(parsed["time_in_label"], "8:30 AM")
        self.assertEqual(parsed["time_out_label"], "12:40 PM")
        self.assertEqual(parsed["break_minutes"], 0)
        self.assertIn("8:30 AM", parsed["interpreted"])

    def test_parse_single_punch_time(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            parsed = parse_manual_punch("8:30 AM last Tuesday")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["date"], "2026-06-09")
        self.assertEqual(parsed["label"], "8:30 AM")
        self.assertIn("08:30", parsed["timestamp"])

    def test_punch_parse_endpoint(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-entry/parse",
                {"kind": "punch", "type": "time-out", "text": "5:15 PM yesterday"},
                format="json",
            )

        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["data"]["label"], "5:15 PM")
        self.assertEqual(response.data["data"]["date"], "2026-06-14")

    def test_parse_natural_language_overnight_and_no_break(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            parsed = parse_manual_entry("10 PM - 6 AM yesterday, no break")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["break_minutes"], 0)
        self.assertEqual(parsed["time_in_label"], "10:00 PM")
        self.assertEqual(parsed["time_out_label"], "6:00 AM")
        self.assertLess(parsed["time_in"], parsed["time_out"])

    def test_parse_endpoint_previews_computed_overtime(self):
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            response = self.client.post(
                "/api/manual-entry/parse",
                {"text": "8:00 AM to 6:00 PM last Tuesday, 30m break"},
                format="json",
            )

        self.assertEqual(response.data["status"], 1)
        data = response.data["data"]
        self.assertEqual(data["break_minutes"], 30)
        self.assertEqual(data["preview"]["gross_hrs"], "10.00")
        self.assertEqual(data["preview"]["net_hrs"], "9.50")
        self.assertEqual(data["preview"]["regular_hrs"], "8.00")
        self.assertEqual(data["preview"]["overtime_hrs"], "1.50")
        self.assertEqual(data["preview"]["total_earnings"], 950.0)

    def test_manual_entry_saves_session_with_break_and_overtime(self):
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW), patch(
            "attendance.intelligence.timezone.now", return_value=FIXED_NOW
        ):
            response = self.client.post(
                "/api/manual-entry",
                {
                    "time_in": "2026-06-09T08:30:00+08:00",
                    "time_out": "2026-06-09T18:15:00+08:00",
                    "break_minutes": 30,
                },
                format="json",
            )

        self.assertEqual(response.data["status"], 1, response.data)
        record = Record.objects.get(user=self.user)
        self.assertEqual(record.total_hrs, Decimal("9.25"))
        self.assertEqual(record.regular_hrs, Decimal("8.00"))
        self.assertEqual(record.overtime_hrs, Decimal("1.25"))
        self.assertEqual(record.earnings, Decimal("925.00"))
        break_row = Break.objects.get(record=record)
        self.assertEqual(break_row.duration_hrs, Decimal("0.50"))

    # ------------------------------------------------------------------
    # 2. Anomaly detection
    # ------------------------------------------------------------------
    def test_long_shift_requires_confirmation_then_saves(self):
        payload = {
            "time_in": "2026-06-09T06:00:00+08:00",
            "time_out": "2026-06-09T21:00:00+08:00",
            "break_minutes": 0,
        }
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW), patch(
            "attendance.intelligence.timezone.now", return_value=FIXED_NOW
        ):
            first = self.client.post("/api/manual-entry", payload, format="json")

        self.assertEqual(first.data["status"], 0)
        self.assertTrue(first.data["data"]["requires_confirmation"])
        codes = [w["code"] for w in first.data["data"]["warnings"]]
        self.assertIn("long-shift", codes)
        self.assertIn("no-break", codes)
        self.assertFalse(Record.objects.filter(user=self.user).exists())

        confirmed = {**payload, "confirm": True,
                     "confirm_token": first.data["data"]["confirm_token"]}
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW), patch(
            "attendance.intelligence.timezone.now", return_value=FIXED_NOW
        ):
            second = self.client.post("/api/manual-entry", confirmed, format="json")

        self.assertEqual(second.data["status"], 1, second.data)
        self.assertTrue(Record.objects.filter(user=self.user).exists())

    def test_overlapping_session_is_flagged(self):
        self._make_record("2026-06-09T08:00:00+08:00", "2026-06-09T12:00:00+08:00")
        with patch("attendance.intelligence.timezone.now", return_value=FIXED_NOW):
            warnings = self.client.post(
                "/api/manual-entry/parse",
                {"text": "9 AM - 11 AM last Tuesday"},
                format="json",
            )
        self.assertIn(
            "overlap", [w["code"] for w in warnings.data["data"]["warnings"]]
        )

    def _make_record(self, time_in, time_out):
        return Record.objects.create(
            user=self.user,
            time_in=time_in,
            time_out=time_out,
            total_hrs=Decimal("4.00"),
            rate_per_hr=Decimal("100.00"),
            overtime_rate_per_hr=Decimal("100.00"),
            regular_hrs=Decimal("4.00"),
            overtime_hrs=Decimal("0.00"),
            regular_earnings=Decimal("400.00"),
            overtime_earnings=Decimal("0.00"),
            earnings=Decimal("400.00"),
        )

    # ------------------------------------------------------------------
    # 3. Predictive earnings
    # ------------------------------------------------------------------
    def test_earnings_include_forecast(self):
        self._make_record("2026-06-08T08:00:00+08:00", "2026-06-08T12:00:00+08:00")
        self._make_record("2026-06-09T08:00:00+08:00", "2026-06-09T12:00:00+08:00")
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            response = self.client.get("/api/earnings")

        data = response.data["data"]
        self.assertEqual(data["forecast_basis"], "Based on your last 8 weeks of earnings")
        self.assertGreater(data["forecast_month"], data["this_month"])
        self.assertGreater(data["forecast_week"], data["this_week"])

    # ------------------------------------------------------------------
    # 4. Nudge inputs on dashboard
    # ------------------------------------------------------------------
    def test_dashboard_exposes_nudge_inputs(self):
        with patch("attendance.services.timezone.now", return_value=FIXED_NOW):
            self.client.post(
                "/api/time-in",
                {"timestamp": "2026-06-15T08:00:00+08:00"},
                format="json",
            )
            response = self.client.get("/api/dashboard")

        data = response.data["data"]
        self.assertEqual(data["overtime_threshold_hrs"], "8.00")
        self.assertEqual(data["overtime_rate_per_hr"], "100.00")
        self.assertEqual(data["session_break_minutes"], 0)
        self.assertTrue(data["clocked_in"])
