"""Model mirroring Laravel's `records` table (App\\Models\\Record)."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


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
    total_hrs = models.DecimalField(
        max_digits=5,
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

    def __str__(self):
        return f"Record {self.id} — user {self.user_id} @ {self.time_in}"

    def clean(self):
        """Model-level validation mirroring the intended attendance rules."""
        super().clean()
        errors = {}

        if self.time_out is not None and self.time_in is not None:
            if self.time_out < self.time_in:
                errors["time_out"] = "Time out cannot be earlier than time in."

        if self.total_hrs is not None and self.total_hrs < 0:
            errors["total_hrs"] = "Total hours cannot be negative."

        if errors:
            raise ValidationError(errors)
