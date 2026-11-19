# TimeBud Django Backend

Django 5.2 LTS replacement for the TimeBud Laravel API. This repository currently
contains the **project skeleton only** — no business modules are implemented yet.

The API contract to implement next lives in `../API_CONTRACT_PART1.md` …
`../API_CONTRACT_PART4.md`.

## Stack

| Component | Version / choice |
|-----------|------------------|
| Python | 3.14 (tested) |
| Django | 5.2.19 (LTS) |
| Django REST Framework | 3.16.1 |
| PostgreSQL driver | psycopg 3.2.13 (binary) |
| Env config | django-environ 0.12.0 |
| CORS | django-cors-headers 4.7.0 |
| API docs | drf-spectacular 0.28.0 |
| WSGI server | gunicorn 23.0.0 |


## Setup

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Create your local environment file
Copy-Item .env.example .env
# Edit .env: set SECRET_KEY and the PostgreSQL credentials for your machine

# 4. Create the development database (run as a PostgreSQL superuser)
#    createdb -h 127.0.0.1 -U postgres timebud

# 5. Verify configuration and apply migrations
python manage.py check
python manage.py migrate
python manage.py runserver
```

## Available endpoints (skeleton only)

| URL | Purpose |
|-----|---------|
| `/api/health/` | Service + PostgreSQL connectivity smoke test |
| `/api/schema/` | OpenAPI 3 schema (drf-spectacular) |
| `/api/docs/` | Swagger UI |
| `/api/redoc/` | ReDoc |

Business endpoints (`/api/login`, `/api/register`, `/api/dashboard`, `/api/time-in`,
`/api/time-out`, `/api/records`, `/api/records/{id}`, `/api/earnings`,
`/api/manual-time-in`, `/api/manual-time-out`, `/api/change-password`,
`/api/logout`, `/api/forgot-password`, `/api/reset-password`) will be added under
`config/api/urls.py` as each module is implemented.

## Contract safeguards already configured

- All routes under the existing `/api` prefix.
- Response envelope helpers reproduce `{status, message, data}` and omit `data`
  where Laravel does.
- Centralized exception handler returns HTTP 200 `status: 0` for validation and
  `DomainError`, and HTTP 401 `{"message":"Unauthenticated."}` for auth failures.
- `TIME_ZONE = "Asia/Manila"`, `USE_TZ = True` (matches Laravel).
- Bearer token authentication via DRF `TokenAuthentication` (Sanctum-compatible).
- CORS origins: local Vite dev servers plus the deployed Vercel frontend.
- PostgreSQL only; SQLite is intentionally not configured.
