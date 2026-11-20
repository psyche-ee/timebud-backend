"""Serializer for the raw Record representation used by Laravel endpoints."""

from rest_framework import serializers

from config.api.serialization import laravel_datetime, to_number

from .models import Record


class RecordSerializer(serializers.ModelSerializer):
    """Render an Eloquent ``Record`` model: decimal strings, ISO-8601 dates."""

    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    time_in = serializers.SerializerMethodField()
    time_out = serializers.SerializerMethodField()
    total_hrs = serializers.SerializerMethodField()
    earnings = serializers.SerializerMethodField()
    regular_hrs = serializers.SerializerMethodField()
    overtime_hrs = serializers.SerializerMethodField()
    regular_earnings = serializers.SerializerMethodField()
    overtime_earnings = serializers.SerializerMethodField()
    overtime_rate_per_hr = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = [
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
        ]

    def get_time_in(self, obj):
        return laravel_datetime(obj.time_in)

    def get_time_out(self, obj):
        return laravel_datetime(obj.time_out)

    def get_total_hrs(self, obj):
        from config.api.serialization import decimal_string

        return decimal_string(obj.total_hrs)

    def get_earnings(self, obj):
        value = getattr(obj, "earnings", None)
        if value is None:
            value = (obj.total_hrs or 0) * (obj.user.rate_per_hr or 0)
        return to_number(value)

    def get_regular_hrs(self, obj):
        from config.api.serialization import decimal_string

        return decimal_string(getattr(obj, "regular_hrs", 0))

    def get_overtime_hrs(self, obj):
        from config.api.serialization import decimal_string

        return decimal_string(getattr(obj, "overtime_hrs", 0))

    def get_regular_earnings(self, obj):
        return to_number(getattr(obj, "regular_earnings", 0))

    def get_overtime_earnings(self, obj):
        return to_number(getattr(obj, "overtime_earnings", 0))

    def get_overtime_rate_per_hr(self, obj):
        from config.api.serialization import decimal_string

        return decimal_string(getattr(obj, "overtime_rate_per_hr", 0))

    def get_created_at(self, obj):
        return laravel_datetime(obj.created_at)

    def get_updated_at(self, obj):
        return laravel_datetime(obj.updated_at)
