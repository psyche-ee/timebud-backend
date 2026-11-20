"""Models mirroring Laravel's users / password_reset_tokens /
personal_access_tokens / sessions tables.

Column names, types, nullability, defaults, unique constraints and indexes are
kept identical to the Laravel migrations. Columns that Django's auth framework
adds (last_login, is_superuser, is_active, is_staff, M2M permission tables) are
additive and do not alter any Laravel column.
"""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

GENDER_CHOICES = [
    ("male", "male"),
    ("female", "female"),
    ("other", "other"),
]


class User(AbstractBaseUser, PermissionsMixin):
    """Mirror of Laravel's `users` table (App\\Models\\User)."""

    # Explicitly declared so Django's default BigAutoField/Laravel id match.
    id = models.BigAutoField(primary_key=True)

    # AbstractBaseUser provides `password` as varchar(128); Laravel uses 255.
    password = models.CharField(max_length=255)

    email = models.EmailField(max_length=255, unique=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    f_name = models.CharField(max_length=255)
    l_name = models.CharField(max_length=255)
    m_name = models.CharField(max_length=255, null=True, blank=True)

    dob = models.DateField()
    gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        default="other",
    )

    e_signature = models.CharField(max_length=255, null=True, blank=True)

    total_hrs_worked = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    rate_per_hr = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    # Overtime rules. Overtime applies to hours worked beyond
    # ``overtime_threshold_hrs`` in a single shift (per-record daily rule).
    overtime_threshold_hrs = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=8,
        validators=[MinValueValidator(0)],
    )
    # If zero, falls back to ``rate_per_hr``. Snapshot is taken per record on
    # clock-out so historical overtime earnings stay stable.
    overtime_rate_per_hr = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    remember_token = models.CharField(max_length=100, null=True, blank=True)

    # Additive fields required by Django admin/auth (not present in Laravel).
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Laravel timestamps() column names.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["f_name", "l_name", "dob"]

    class Meta:
        db_table = "users"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gender__in=[choice[0] for choice in GENDER_CHOICES]),
                name="users_gender_check",
            ),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        if self.dob and self.dob > timezone.localdate():
            raise ValidationError({"dob": "Date of birth cannot be in the future."})


class PasswordResetToken(models.Model):
    """Mirror of Laravel's `password_reset_tokens` table."""

    email = models.EmailField(max_length=255, primary_key=True)
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "password_reset_tokens"
        verbose_name = "Password reset token"
        verbose_name_plural = "Password reset tokens"

    def __str__(self):
        return self.email


class PersonalAccessToken(models.Model):
    """Mirror of Laravel Sanctum's `personal_access_tokens` table."""

    id = models.BigAutoField(primary_key=True)

    # Laravel morphs('tokenable') → varchar + unsignedBigInteger + composite index.
    tokenable_type = models.CharField(max_length=255)
    tokenable_id = models.BigIntegerField()

    name = models.TextField()
    token = models.CharField(max_length=64, unique=True)
    abilities = models.TextField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "personal_access_tokens"
        verbose_name = "Personal access token"
        verbose_name_plural = "Personal access tokens"
        indexes = [
            models.Index(
                fields=["tokenable_type", "tokenable_id"],
                name="pat_tokenable_idx",
            ),
            models.Index(
                fields=["expires_at"],
                name="pat_expires_at_idx",
            ),
        ]

    def __str__(self):
        return f"{self.tokenable_type}:{self.tokenable_id}"


class Session(models.Model):
    """Mirror of Laravel's `sessions` table.

    Laravel declares `foreignId('user_id')->nullable()->index()` without
    `->constrained()`, so there is NO foreign key constraint — only an index.
    This model deliberately keeps `user_id` as a plain indexed BigIntegerField.
    """

    id = models.CharField(max_length=255, primary_key=True)
    user_id = models.BigIntegerField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    payload = models.TextField()
    last_activity = models.IntegerField()

    class Meta:
        db_table = "sessions"
        indexes = [
            models.Index(fields=["user_id"], name="sessions_user_id_index"),
            models.Index(fields=["last_activity"], name="sessions_last_activity_index"),
        ]
