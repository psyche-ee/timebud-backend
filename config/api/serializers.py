"""Shared serializer base reproducing Laravel's request normalization.

Laravel's global ``ConvertEmptyStringsToNull`` middleware turns every empty
string in a request body into ``null`` before validation. DRF does not do this,
so all TimeBud request serializers inherit this small override to keep the
validation behavior identical.
"""

from rest_framework import serializers


class LaravelStyleSerializer(serializers.Serializer):
    """Convert empty-string inputs to ``None`` before DRF validates them."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = {
                key: None if value == "" else value
                for key, value in data.items()
            }
        return super().to_internal_value(data)
