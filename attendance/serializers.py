"""Serializer for the raw Record representation used by Laravel endpoints."""

from rest_framework import serializers

from config.api.serialization import laravel_datetime

from .models import Record


class RecordSerializer(serializers.ModelSerializer):
    """Render an Eloquent ``Record`` model: decimal strings, ISO-8601 dates."""

    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    time_in = serializers.SerializerMethodField()
    time_out = serializers.SerializerMethodField()
    total_hrs = serializers.SerializerMethodField()
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

    def get_created_at(self, obj):
        return laravel_datetime(obj.created_at)

    def get_updated_at(self, obj):
        return laravel_datetime(obj.updated_at)
