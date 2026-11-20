"""Tests for the Laravel-compatible authentication endpoints."""

import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import services
from accounts.models import PasswordResetToken, PersonalAccessToken, User
from attendance.models import Record


def register_payload(**overrides):
    payload = {
        "f_name": "Juan",
        "l_name": "Dela Cruz",
        "m_name": "Santos",
        "email": "juan@example.com",
        "password": "secret123",
        "confirm_password": "secret123",
        "dob": "2000-01-31",
        "gender": "male",
        "rate_per_hr": "250.50",
    }
    payload.update(overrides)
    return payload


class AuthTests(APITestCase):
    def test_register_success_matches_laravel_payload(self):
        response = self.client.post("/api/register", register_payload(), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "User registered successfully")
        data = response.data["data"]
        self.assertEqual(
            list(data.keys()),
            [
                "token",
                "f_name",
                "l_name",
                "m_name",
                "email",
                "gender",
                "dob",
                "rate_per_hr",
            ],
        )
        self.assertEqual(data["dob"], "2000-01-31")
        self.assertEqual(data["rate_per_hr"], "250.50")
        self.assertNotIn("user_id", data)

        token_id, _, plain = data["token"].partition("|")
        self.assertTrue(token_id and plain)
        token_row = PersonalAccessToken.objects.get(pk=int(token_id))
        self.assertEqual(token_row.name, "PaylioApp")
        self.assertEqual(token_row.token, hashlib.sha256(plain.encode()).hexdigest())
        self.assertEqual(token_row.abilities, '["*"]')
        self.assertEqual(token_row.tokenable_type, "App\\Models\\User")

        user = User.objects.get(email="juan@example.com")
        self.assertTrue(check_password("secret123", user.password))
        self.assertTrue(user.password.startswith("bcrypt$"))

    def test_register_defaults_missing_rate_to_zero(self):
        payload = register_payload()
        payload.pop("rate_per_hr")
        response = self.client.post("/api/register", payload, format="json")
        self.assertEqual(response.data["data"]["rate_per_hr"], "0.00")

    def test_register_validation_returns_flat_laravel_messages(self):
        response = self.client.post(
            "/api/register",
            register_payload(
                f_name="",
                email="not-an-email",
                password="123",
                confirm_password="different",
                gender="alien",
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "Validation Error")
        self.assertIsInstance(response.data["data"], list)
        self.assertIn("The f name field is required.", response.data["data"])
        self.assertIn("The email must be a valid email address.", response.data["data"])
        self.assertIn("The password must be at least 6 characters.", response.data["data"])
        self.assertIn(
            "The selected gender is invalid.", response.data["data"]
        )

    def test_register_duplicate_email(self):
        self.client.post("/api/register", register_payload(), format="json")
        response = self.client.post("/api/register", register_payload(), format="json")
        self.assertEqual(response.data["status"], 0)
        self.assertIn("The email has already been taken.", response.data["data"])

    def test_login_success_includes_user_id_and_timebud_token(self):
        self.client.post("/api/register", register_payload(), format="json")
        response = self.client.post(
            "/api/login",
            {"email": "juan@example.com", "password": "secret123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "User logged in successfully")
        self.assertEqual(response.data["data"]["user_id"], User.objects.get().id)
        token_id, _, _ = response.data["data"]["token"].partition("|")
        self.assertEqual(
            PersonalAccessToken.objects.get(pk=int(token_id)).name, "TimeBud"
        )

    def test_login_validation_error(self):
        response = self.client.post("/api/login", {"email": "bad"}, format="json")
        self.assertEqual(response.data["message"], "Validation Error")
        self.assertIsInstance(response.data["data"], list)
        self.assertIn("The email must be a valid email address.", response.data["data"])
        self.assertIn("The password field is required.", response.data["data"])

    def test_login_rejects_bad_and_unknown_credentials(self):
        self.client.post("/api/register", register_payload(), format="json")
        for password in ("wrong-password",):
            response = self.client.post(
                "/api/login",
                {"email": "juan@example.com", "password": password},
                format="json",
            )
            self.assertEqual(response.data["status"], 0)
            self.assertEqual(response.data["message"], "Invalid credentials")
            self.assertIsNone(response.data["data"])

        # Unknown email is handled gracefully (Laravel would raise a 500 here).
        response = self.client.post(
            "/api/login",
            {"email": "nobody@example.com", "password": "secret123"},
            format="json",
        )
        self.assertEqual(response.data["message"], "Invalid credentials")

    def _authenticated_client(self):
        response = self.client.post("/api/register", register_payload(), format="json")
        token = response.data["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return User.objects.get(email="juan@example.com"), token

    def test_logout_deletes_only_presented_token(self):
        user, token = self._authenticated_client()
        other_token = services.create_token(user, "OtherDevice")

        response = self.client.post("/api/logout", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "User logged out successfully")
        self.assertIsNone(response.data["data"])

        self.assertEqual(PersonalAccessToken.objects.count(), 1)
        self.assertTrue(
            PersonalAccessToken.objects.filter(
                pk=int(other_token.partition("|")[0])
            ).exists()
        )
        self.assertFalse(
            PersonalAccessToken.objects.filter(
                pk=int(token.partition("|")[0])
            ).exists()
        )

    def test_unauthenticated_protected_request(self):
        response = self.client.post("/api/logout", {}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {"message": "Unauthenticated."})

    def test_change_password_success_and_wrong_current(self):
        self._authenticated_client()
        response = self.client.post(
            "/api/change-password",
            {
                "current_password": "secret123",
                "new_password": "newsecret123",
                "confirm_new_password": "newsecret123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Password changed successfully")
        self.assertIsNone(response.data["data"])

        wrong = self.client.post(
            "/api/change-password",
            {
                "current_password": "secret123",
                "new_password": "another123",
                "confirm_new_password": "another123",
            },
            format="json",
        )
        self.assertEqual(wrong.data["message"], "Current password is incorrect")

    def test_change_password_validation_mismatch(self):
        self._authenticated_client()
        response = self.client.post(
            "/api/change-password",
            {
                "current_password": "secret123",
                "new_password": "newsecret123",
                "confirm_new_password": "does-not-match",
            },
            format="json",
        )
        self.assertEqual(response.data["message"], "Validation Error")
        self.assertIn(
            "The confirm new password and new password must match.",
            response.data["data"],
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_forgot_password_success(self):
        self.client.post("/api/register", register_payload(), format="json")
        response = self.client.post(
            "/api/forgot-password",
            {"email": "juan@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(
            response.data["message"], "Password reset link sent to your email"
        )
        self.assertNotIn("data", response.data)

        record = PasswordResetToken.objects.get(email="juan@example.com")
        self.assertEqual(len(record.token), 64)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset Your Password")
        self.assertIn(record.token, mail.outbox[0].body)

    def test_forgot_password_unknown_user(self):
        response = self.client.post(
            "/api/forgot-password",
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.data["status"], 0)
        self.assertEqual(response.data["message"], "User not found")
        self.assertNotIn("data", response.data)

    def test_reset_password_success_is_single_use(self):
        self.client.post("/api/register", register_payload(), format="json")
        self.client.post(
            "/api/forgot-password", {"email": "juan@example.com"}, format="json"
        )
        token = PasswordResetToken.objects.get(email="juan@example.com").token

        response = self.client.post(
            "/api/reset-password",
            {
                "token": token,
                "password": "resetpass123",
                "password_confirmation": "resetpass123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["message"], "Password successfully reset")
        self.assertNotIn("data", response.data)
        self.assertFalse(PasswordResetToken.objects.filter(token=token).exists())

        login = self.client.post(
            "/api/login",
            {"email": "juan@example.com", "password": "resetpass123"},
            format="json",
        )
        self.assertEqual(login.data["status"], 1)

        reuse = self.client.post(
            "/api/reset-password",
            {
                "token": token,
                "password": "another123",
                "password_confirmation": "another123",
            },
            format="json",
        )
        self.assertEqual(reuse.data["message"], "Invalid reset token")

    def test_reset_password_rejects_expired_token(self):
        self.client.post("/api/register", register_payload(), format="json")
        PasswordResetToken.objects.create(
            email="juan@example.com",
            token="expired-token",
            created_at=timezone.now() - timedelta(minutes=61),
        )
        response = self.client.post(
            "/api/reset-password",
            {
                "token": "expired-token",
                "password": "resetpass123",
                "password_confirmation": "resetpass123",
            },
            format="json",
        )
        self.assertEqual(
            response.data["message"],
            "Reset link has expired. Please request a new one.",
        )
        self.assertFalse(PasswordResetToken.objects.filter(token="expired-token").exists())

    def test_reset_password_validation_uses_password_confirmation(self):
        response = self.client.post(
            "/api/reset-password",
            {"token": "abc", "password": "secret123"},
            format="json",
        )
        self.assertEqual(response.data["message"], "Validation Error")
        self.assertIn(
            "The password confirmation does not match.", response.data["data"]
        )

    def test_profile_update_persists_rate_and_backfills_zero_rate_records(self):
        user, _ = self._authenticated_client()
        user.rate_per_hr = 0
        user.save(update_fields=["rate_per_hr"])

        record = Record.objects.create(
            user=user,
            time_in=timezone.now() - timedelta(hours=2),
            time_out=timezone.now(),
            total_hrs="2.00",
            rate_per_hr=0,
            earnings=0,
        )

        response = self.client.put(
            "/api/profile",
            {
                "f_name": "Pedro",
                "l_name": "Penduko",
                "email": "pedro@example.com",
                "dob": "1999-05-01",
                "gender": "male",
                "rate_per_hr": "100.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], 1)
        self.assertEqual(response.data["data"]["rate_per_hr"], "100.00")
        self.assertEqual(response.data["data"]["email"], "pedro@example.com")

        user.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(user.rate_per_hr, 100)
        self.assertEqual(record.rate_per_hr, 100)
        self.assertEqual(record.earnings, 200)

        earnings = self.client.get("/api/earnings")
        self.assertEqual(earnings.data["data"]["total"], 200.0)

    def test_profile_update_persists_overtime_settings(self):
        user, _ = self._authenticated_client()
        response = self.client.put(
            "/api/profile",
            {
                "f_name": "Juan",
                "l_name": "Dela Cruz",
                "email": "juan@example.com",
                "dob": "2000-01-31",
                "gender": "male",
                "rate_per_hr": "100.00",
                "overtime_threshold_hrs": "8.00",
                "overtime_rate_per_hr": "150.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["rate_per_hr"], "100.00")
        self.assertEqual(response.data["data"]["overtime_threshold_hrs"], "8.00")
        self.assertEqual(response.data["data"]["overtime_rate_per_hr"], "150.00")

        user.refresh_from_db()
        self.assertEqual(user.overtime_threshold_hrs, Decimal("8.00"))
        self.assertEqual(user.overtime_rate_per_hr, Decimal("150.00"))

    def test_blank_overtime_rate_uses_regular_rate_sentinel(self):
        user, _ = self._authenticated_client()
        response = self.client.put(
            "/api/profile",
            {
                "f_name": "Juan",
                "l_name": "Dela Cruz",
                "email": "juan@example.com",
                "dob": "2000-01-31",
                "gender": "male",
                "rate_per_hr": "100.00",
                "overtime_rate_per_hr": None,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["overtime_rate_per_hr"], "0.00")
        user.refresh_from_db()
        self.assertEqual(user.overtime_rate_per_hr, Decimal("0.00"))


@override_settings(GOOGLE_CLIENT_ID="test-client-id.apps.googleusercontent.com")
class GoogleLoginTests(APITestCase):
    IDINFO = {
        "iss": "accounts.google.com",
        "email_verified": True,
        "email": "juanauth@example.com",
        "given_name": "Juan",
        "family_name": "Dela Cruz",
        "sub": "1234567890",
    }

    @patch("accounts.services.id_token.verify_oauth2_token")
    def test_google_login_creates_user_and_token(self, mock_verify):
        mock_verify.return_value = self.IDINFO

        user, token = services.google_login("any-credential")

        mock_verify.assert_called_once()
        self.assertEqual(user.email, "juanauth@example.com")
        self.assertEqual(user.f_name, "Juan")
        self.assertEqual(user.l_name, "Dela Cruz")
        # Google accounts have no password; mark it unusable.
        self.assertTrue(user.password.startswith("!"))
        self.assertIsNotNone(token)

    @patch("accounts.services.id_token.verify_oauth2_token")
    def test_google_login_reuses_existing_user(self, mock_verify):
        mock_verify.return_value = self.IDINFO

        services.google_login("any-credential")
        user2, _ = services.google_login("any-credential")

        self.assertEqual(
            User.objects.filter(email="juanauth@example.com").count(), 1
        )
        self.assertEqual(user2.email, "juanauth@example.com")

    @patch("accounts.services.id_token.verify_oauth2_token")
    def test_google_login_rejects_invalid_token(self, mock_verify):
        mock_verify.side_effect = ValueError("Invalid token")

        with self.assertRaises(ValueError):
            services.google_login("bad-credential")
