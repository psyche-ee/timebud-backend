"""Model mirroring Laravel's `records` table (App\\Models\\Record)."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Record(models.Model):
    """A single DTR entry: one clock-in and its optional clock-out."""

    id = models.BigAutoField(primary_key=True)

    # Laravel: foreignId('user_id')->constrained()->onDelete('cascade')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="records",
    )

    time_in = models.DateTimeField()
    time_out = models.DateTimeField(null=True, blank=True)

    # Laravel: decimal('total_hrs', 5, 2)->default(0)
    # Net hours actually worked (gross elapsed minus break time).
    total_hrs = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # Earnings breakdown snapshots taken when the session closes.
    regular_hrs = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    overtime_hrs = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    regular_earnings = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    overtime_earnings = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    overtime_rate_per_hr = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    # Immutable snapshot taken when the session is closed. Keeping the rate and
    # amount on the record prevents historical earnings from changing when a
    # user updates their current hourly rate.
    rate_per_hr = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    earnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "records"
        # Laravel's Eloquent latest() defaults to the created_at column.
        get_latest_by = "created_at"
        indexes = [
            models.Index(fields=["user", "time_in"], name="records_user_time_in_idx"),
            models.Index(fields=["user", "created_at"], name="records_user_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_hrs__gte=0), name="records_total_hrs_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(regular_hrs__gte=0), name="records_regular_hrs_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(overtime_hrs__gte=0), name="records_overtime_hrs_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(rate_per_hr__gte=0), name="records_rate_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(overtime_rate_per_hr__gte=0),
                name="records_overtime_rate_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(regular_earnings__gte=0), name="records_regular_earnings_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(overtime_earnings__gte=0), name="records_overtime_earnings_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(earnings__gte=0), name="records_earnings_nonnegative"
            ),
        ]

    def __str__(self):
        return f"Record {self.id} — user {self.user_id} @ {self.time_in}"

    def clean(self):
        """Model-level validation mirroring the intended attendance rules."""
        super().clean()
        errors = {}

        if self.time_out is not None and self.time_in is not None:
            if self.time_out < self.time_in:
                errors["time_out"] = "Time out cannot be earlier than time in."

        for field, label in [
            ("total_hrs", "Total hours"),
            ("regular_hrs", "Regular hours"),
            ("overtime_hrs", "Overtime hours"),
        ]:
            value = getattr(self, field, None)
            if value is not None and value < 0:
                errors[field] = f"{label} cannot be negative."

        if errors:
            raise ValidationError(errors)


class Break(models.Model):
    """A paid/unpaid break within a single open record.

    Break time is subtracted from gross elapsed time when ``total_hrs`` is
    finalized on clock-out, so breaks reduce billable/worked hours.
    """

    id = models.BigAutoField(primary_key=True)

    # Laravel: foreignId('record_id')->constrained()->onDelete('cascade')
    record = models.ForeignKey(
        Record,
        on_delete=models.CASCADE,
        db_column="record_id",
        related_name="breaks",
    )

    time_in = models.DateTimeField()
    time_out = models.DateTimeField(null=True, blank=True)

    # decimal('duration_hrs', 5, 2)->default(0)
    duration_hrs = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "breaks"
        indexes = [
            models.Index(fields=["record", "time_in"], name="breaks_record_time_in_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(duration_hrs__gte=0), name="breaks_duration_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(time_out__gte=models.F("time_in"))
                | Q(time_out__isnull=True),
                name="breaks_time_out_after_time_in",
            ),
        ]

    def __str__(self):
        return f"Break {self.id} — record {self.record_id} @ {self.time_in}"

    def clean(self):
        super().clean()
        errors = {}
        if self.time_out is not None and self.time_in is not None:
            if self.time_out < self.time_in:
                errors["time_out"] = "Break time out cannot be earlier than time in."
        if errors:
            raise ValidationError(errors)
