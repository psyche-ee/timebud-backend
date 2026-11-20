"""Development settings — local PostgreSQL and permissive local CORS origins."""

import socket

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env  # noqa: F401

DEBUG = True


def _local_ipv4_addresses():
    """Pick up the machine's LAN IPs so phones can reach the dev servers."""
    addresses = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


LOCAL_IPS = _local_ipv4_addresses()

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=[
        "localhost",
        "127.0.0.1",
        "[::1]",
        "testserver",
        *LOCAL_IPS,
    ],
)

# Local Vite dev server plus the production frontend origin.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://timebud-frontend.vercel.app",
        *[f"http://{address}:5173" for address in LOCAL_IPS],
    ],
)

# Also accept any private-network origin, so the LAN IP does not have to be
# known ahead of time (useful when testing from a phone on Wi-Fi).
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^http://(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?$",
]

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
