# TimeBud Django Backend

Django 5.2 LTS API for the TimeBud daily time record system. It replaces the
original Laravel/Sanctum backend while preserving the `{status, message, data}`
response envelope, bearer tokens, and `Asia/Manila` time handling.

## Stack

| Component | Version / choice |
|-----------|------------------|
| Python | 3.14 (tested locally), 3.11–3.13 also supported |
| Django | 5.2.19 (LTS) |
| Django REST Framework | 3.16.1 |
| PostgreSQL driver | psycopg 3.2.13 (binary) |
| Env config | django-environ 0.12.0 |
| CORS | django-cors-headers 4.7.0 |
| API docs | drf-spectacular 0.28.0 |
| WSGI server | gunicorn 23.0.0 |
| Auth | bcrypt 4.3.0, google-auth 2.62.2 |

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: SECRET_KEY, and either DATABASE_URL or the DB_* values
python manage.py check
python manage.py migrate
python manage.py runserver
```

`DATABASE_URL` takes precedence over `DB_NAME` / `DB_USER` / `DB_PASSWORD` /
`DB_HOST` / `DB_PORT`. SSL options in the URL (`sslmode=require`,
`channel_binding=require`) are passed through to psycopg automatically.

## Endpoints

| URL | Purpose |
|-----|---------|
| `/api/health/` | Public service + PostgreSQL connectivity check |
| `/api/register`, `/api/login`, `/api/logout` | Authentication |
| `/api/auth/google`, `/api/forgot-password`, `/api/reset-password` | Google / password flows |
| `/api/profile`, `/api/change-password` | Account settings |
| `/api/dashboard`, `/api/earnings`, `/api/records` | Reporting |
| `/api/time-in`, `/api/time-out`, `/api/break-in`, `/api/break-out` | Punch/break actions |
| `/api/manual-entry`, `/api/manual-entry/parse`, `/api/manual-time-*` | Manual entries |
| `/api/schema/`, `/api/docs/`, `/api/redoc/` | OpenAPI schema and docs |

## Deployment on Render (Neon PostgreSQL)

The repository includes `render.yaml` (Blueprint) and `render-start.sh`.

1. Push this backend to Git. In Render, choose **New → Blueprint** and select
   the repo; Render reads `render.yaml` from the repository root.
2. When prompted, set `DATABASE_URL` to the Neon connection string. Keep it as
   a secret env var — never commit it. The Blueprint leaves this as `sync: false`
   so you paste it in the Render Dashboard.
3. Render generates `SECRET_KEY` automatically. Review the remaining env vars:
   - `ALLOWED_HOSTS` / `RENDER_EXTERNAL_HOSTNAME` — the service hostname is
     added automatically; update `ALLOWED_HOSTS` if you use a custom domain.
   - `CORS_ALLOWED_ORIGINS` / `FRONTEND_URL` — set to your real frontend URL(s).
   - `GOOGLE_CLIENT_ID` — must match the ID used by the deployed frontend, and
     the Render/frontend origins must be authorized in Google Cloud Console.
4. Deploy. `render-start.sh` runs `manage.py migrate --noinput` before starting
   gunicorn on `$PORT`. Migrations were pre-applied to the current Neon database,
   so subsequent deploys are no-ops.
5. Verify `https://<your-service>.onrender.com/api/health/` returns
   `{"status":1,...,"database":"connected"}`.

Notes:
- `SECURE_SSL_REDIRECT` is off because Render terminates TLS at the edge and
  its health probe uses HTTP; HTTPS is enforced at the Render boundary.
- `CONN_MAX_AGE=0` and connection health checks are used because Neon/its
  pooler can close idle connections.
- Password reset emails fall back to console logging until SMTP env vars
  (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`) are set.
- On the free plan the service sleeps after inactivity; the first request
  after a sleep may take a few seconds.

## Contract safeguards

- All routes remain under `/api`.
- Response envelope helpers reproduce `{status, message, data}` and omit `data`
  where Laravel does.
- Centralized exception handler returns HTTP 200 `status: 0` for validation and
  `DomainError`, and HTTP 401 `{"message":"Unauthenticated."}` for auth failures.
- `TIME_ZONE = "Asia/Manila"`, `USE_TZ = True`.
- Bearer token authentication is Sanctum-compatible.
- PostgreSQL only; SQLite is intentionally not configured.
