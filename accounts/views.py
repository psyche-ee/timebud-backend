"""Authentication endpoints mirroring Laravel's AuthController."""

from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from config.api.responses import error_response, success_response
from . import services
from .serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    ProfileSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
)

User = get_user_model()


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = services.register_user(serializer.validated_data)
        token = services.create_token(user, "PaylioApp")
        return success_response(
            data=services.auth_payload(user, token),
            message="User registered successfully",
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user is None or not services.passwords_match(
            serializer.validated_data["password"], user.password
        ):
            return error_response(message="Invalid credentials", data=None)

        token = services.create_token(user, "TimeBud")
        return success_response(
            data=services.auth_payload(user, token, include_user_id=True),
            message="User logged in successfully",
        )


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(data=services.profile_payload(request.user))

    def put(self, request):
        return self._update(request)

    def patch(self, request):
        return self._update(request)

    def _update(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = services.update_profile(request.user, serializer.validated_data)
        return success_response(
            data=services.profile_payload(user),
            message="Profile updated successfully",
        )


class GoogleLoginView(APIView):
    """Sign in / sign up with a Google ID token (Sign In with Google).

    The frontend obtains ``credential`` from Google Identity Services and POSTs
    it here. The server verifies the signature/audience with Google's public
    keys, gets or creates the user by email, and issues the same Sanctum-style
    token as email/password login.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        credential = request.data.get("credential")
        if not credential:
            return error_response(message="Google credential is required", data=None)

        try:
            user, token = services.google_login(credential)
        except ValueError as error:
            return error_response(message=str(error), data=None)

        return success_response(
            data=services.auth_payload(user, token, include_user_id=True),
            message="Signed in with Google successfully",
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Sanctum revokes only the token that was presented.
        if request.auth is not None:
            request.auth.delete()
        return success_response(data=None, message="User logged out successfully")


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not services.passwords_match(
            serializer.validated_data["current_password"], user.password
        ):
            return error_response(message="Current password is incorrect", data=None)

        services.change_password(user, serializer.validated_data["new_password"])
        return success_response(data=None, message="Password changed successfully")


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        if not User.objects.filter(email=email).exists():
            return error_response(message="User not found")

        token = services.create_reset_token(email)
        services.send_reset_link(email, token)
        return success_response(message="Password reset link sent to your email")


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status, message = services.consume_reset_token(
            serializer.validated_data["token"],
            serializer.validated_data["password"],
        )
        if status == "ok":
            return success_response(message=message)
        return error_response(message=message)
