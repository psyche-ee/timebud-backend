"""Production settings — secure defaults, no localhost origins by default."""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

# Render exposes the generated onrender.com hostname here, so deployments work
# even when the service is renamed or a preview deployment is used.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
if _render_host := env("RENDER_EXTERNAL_HOSTNAME", default=""):
    ALLOWED_HOSTS.append(_render_host)

SECRET_KEY = env("SECRET_KEY")  # required in production — no insecure fallback

# Only the deployed React frontend is trusted by default.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["https://timebud-frontend.vercel.app"],
)
CORS_ALLOW_CREDENTIALS = False

# Security middleware settings. Render terminates TLS at the edge and its
# health check is HTTP, so Django must not issue redirects itself.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Password resets need a real transport; fall back to console logging until
# SMTP credentials are configured (otherwise startup/deploys still succeed).
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if env("EMAIL_HOST", default="")
        else "django.core.mail.backends.console.EmailBackend"
    ),
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("LOG_LEVEL", default="WARNING"),
    },
}
