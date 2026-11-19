"""Request serializers with Laravel's original validation rules and messages.

Validation errors are intentionally rendered by the global exception handler as
``{"status": 0, "message": "Validation Error", "data": ["...", "..."]}`` with a
flat list of Laravel-style strings.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from config.api.serializers import LaravelStyleSerializer

User = get_user_model()

GENDERS = ("male", "female", "other")


def _required(label):
    return f"The {label} field is required."


def _max(label, limit=255):
    return f"The {label} may not be greater than {limit} characters."


class RegisterSerializer(LaravelStyleSerializer):
    f_name = serializers.CharField(
        max_length=255,
        error_messages={
            "required": _required("f name"),
            "blank": _required("f name"),
            "null": _required("f name"),
            "max_length": _max("f name"),
        },
    )
    l_name = serializers.CharField(
        max_length=255,
        error_messages={
            "required": _required("l name"),
            "blank": _required("l name"),
            "null": _required("l name"),
            "max_length": _max("l name"),
        },
    )
    m_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_null=True,
        allow_blank=True,
        error_messages={"max_length": _max("m name")},
    )
    email = serializers.EmailField(
        max_length=255,
        error_messages={
            "required": _required("email"),
            "blank": _required("email"),
            "null": _required("email"),
            "invalid": "The email must be a valid email address.",
            "max_length": _max("email"),
        },
    )
    password = serializers.CharField(
        min_length=6,
        write_only=True,
        error_messages={
            "required": _required("password"),
            "blank": _required("password"),
            "null": _required("password"),
            "min_length": "The password must be at least 6 characters.",
        },
    )
    confirm_password = serializers.CharField(
        write_only=True,
        error_messages={
            "required": _required("confirm password"),
            "blank": _required("confirm password"),
            "null": _required("confirm password"),
        },
    )
    dob = serializers.DateField(
        error_messages={
            "required": _required("dob"),
            "null": _required("dob"),
            "invalid": "The dob is not a valid date.",
        }
    )
    gender = serializers.ChoiceField(
        choices=GENDERS,
        error_messages={
            "required": _required("gender"),
            "null": _required("gender"),
            "invalid_choice": "The selected gender is invalid.",
        },
    )
    rate_per_hr = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=0,
        error_messages={
            "invalid": "The rate per hr must be a number.",
            "min_value": "The rate per hr must be at least 0.",
            "max_digits": "The rate per hr may not be greater than 10 digits.",
        },
    )

    def validate_confirm_password(self, value):
        if value != self.initial_data.get("password"):
            raise serializers.ValidationError(
                "The confirm password and password must match."
            )
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("The email has already been taken.")
        return value


class LoginSerializer(LaravelStyleSerializer):
    email = serializers.EmailField(
        error_messages={
            "required": _required("email"),
            "blank": _required("email"),
            "null": _required("email"),
            "invalid": "The email must be a valid email address.",
        }
    )
    password = serializers.CharField(
        error_messages={
            "required": _required("password"),
            "blank": _required("password"),
            "null": _required("password"),
        }
    )


class ForgotPasswordSerializer(LaravelStyleSerializer):
    email = serializers.EmailField(
        error_messages={
            "required": _required("email"),
            "blank": _required("email"),
            "null": _required("email"),
            "invalid": "The email must be a valid email address.",
        }
    )


class ResetPasswordSerializer(LaravelStyleSerializer):
    token = serializers.CharField(
        error_messages={
            "required": _required("token"),
            "blank": _required("token"),
            "null": _required("token"),
        }
    )
    password = serializers.CharField(
        min_length=6,
        error_messages={
            "required": _required("password"),
            "blank": _required("password"),
            "null": _required("password"),
            "min_length": "The password must be at least 6 characters.",
        },
    )
    password_confirmation = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    def validate(self, attrs):
        if attrs.get("password_confirmation") != attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "The password confirmation does not match."}
            )
        return attrs


class ChangePasswordSerializer(LaravelStyleSerializer):
    current_password = serializers.CharField(
        error_messages={
            "required": _required("current password"),
            "blank": _required("current password"),
            "null": _required("current password"),
        }
    )
    new_password = serializers.CharField(
        min_length=6,
        error_messages={
            "required": _required("new password"),
            "blank": _required("new password"),
            "null": _required("new password"),
            "min_length": "The new password must be at least 6 characters.",
        },
    )
    confirm_new_password = serializers.CharField(
        error_messages={
            "required": _required("confirm new password"),
            "blank": _required("confirm new password"),
            "null": _required("confirm new password"),
        }
    )

    def validate_confirm_new_password(self, value):
        if value != self.initial_data.get("new_password"):
            raise serializers.ValidationError(
                "The confirm new password and new password must match."
            )
        return value
