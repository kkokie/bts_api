# Deployment Guide

Technical reference for containerizing and deploying the Beat the Streak app.
For prediction logic and app internals, see [README.md](README.md).

---

## Table of Contents

1. [Why Docker?](#why-docker)
2. [What Changed](#what-changed)
3. [How `docker compose up --build` Works](#how-docker-compose-up---build-works)
4. [Settings Split Explained](#settings-split-explained)
5. [Environment Variables](#environment-variables)
6. [Local Dev Workflow](#local-dev-workflow)
7. [Production Deployment (Coolify + Hetzner)](#production-deployment-coolify--hetzner)
8. [File Reference](#file-reference)

---

## Why Docker?

Before containerization, running this app required:

- A specific Python version installed locally
- PostgreSQL installed and configured manually
- A virtual environment with all packages installed in the right order
- Hand-editing `config/settings.py` to point at your local database

This works fine on one machine but breaks the moment you try to deploy to a server or share the project. The server might have a different Python version, no Postgres, or the wrong architecture (e.g. arm64 vs x86_64). The app would also run with `DEBUG=True` and a hardcoded secret key — a security problem in production.

Docker solves this by packaging the app, its dependencies, and its runtime into a single reproducible image. The same image runs identically on a Mac laptop, a Linux VPS, or any CI environment.

---

## What Changed

### New files added

| File | Purpose |
|---|---|
| `Dockerfile` | Defines how to build the app image |
| `docker-compose.yml` | Local dev: runs the app + a Postgres container together |
| `docker-compose.prod.yml` | Production: what Coolify uses on the Hetzner VPS |
| `.dockerignore` | Tells Docker which files to exclude from the image build |
| `requirements.txt` | Pinned Python dependencies (replaces ad-hoc pip installs) |
| `.env.example` | Template showing which env vars are required (safe to commit) |
| `.env` | Actual env var values for local dev (gitignored — never commit this) |
| `config/settings/base.py` | Shared settings for all environments |
| `config/settings/dev.py` | Development overrides (SQLite, DEBUG=True, open ALLOWED_HOSTS) |
| `config/settings/prod.py` | Production overrides (Postgres, DEBUG=False, strict ALLOWED_HOSTS) |
| `config/settings/__init__.py` | Makes the settings directory a Python package |

### Files removed

| File | Reason |
|---|---|
| `config/settings.py` | Replaced by the settings package above |

### Files modified

| File | Change |
|---|---|
| `manage.py` | Default settings module changed to `config.settings.dev` |
| `config/wsgi.py` | Default settings module changed to `config.settings.prod` |
| `config/asgi.py` | Default settings module changed to `config.settings.prod` |
| `.gitignore` | Added `.env`, `db.sqlite3`, `staticfiles/`, `lineup_cron.log` |

### New production dependencies

Three packages were added to `requirements.txt`:

- **`gunicorn`** — production WSGI server. Django's built-in `runserver` is single-threaded and not safe for production. Gunicorn spawns multiple worker processes and handles concurrent requests properly.
- **`whitenoise`** — serves static files (CSS, JS) directly from the Django process. Without this you'd need a separate nginx container just to serve static assets.
- **`django-environ`** — reads configuration from environment variables and a `.env` file using a clean API (`env('SECRET_KEY')`, `env.db('DATABASE_URL')`). Eliminates hardcoded secrets and database credentials from the codebase.

---

## How `docker compose up --build` Works

Running `docker compose up --build` from the project root triggers the following sequence:

### 1. Docker reads `docker-compose.yml`

The compose file defines two services: `db` (Postgres) and `web` (the Django app).

### 2. The `db` service starts immediately

Docker pulls `postgres:16-alpine` (a minimal Postgres image) and starts a Postgres container. The database `bts` is created automatically using the `POSTGRES_*` environment variables. Data is persisted in a named Docker volume (`postgres_data`) so it survives container restarts.

### 3. The `web` service is built

Because `--build` was passed, Docker rebuilds the app image from `Dockerfile` instead of using a cached version. The build steps are:

```
FROM python:3.11-slim          # Start from a clean Python 3.11 image
apt-get install gcc libpq-dev  # System libraries needed to compile psycopg2
pip install -r requirements.txt  # Install all Python dependencies
COPY . .                         # Copy the project files into the image
SECRET_KEY=build-dummy-key \
  python manage.py collectstatic --noinput  # Gather static files into /app/staticfiles/
```

The `collectstatic` step copies all static assets (Django admin CSS/JS, any app static files) into a single `staticfiles/` directory. WhiteNoise then serves these files at runtime without needing a separate web server. A dummy `SECRET_KEY` is used here because Django requires one to start up, but no real secrets are needed at build time.

### 4. The `web` service starts

Once the image is built, Docker starts the web container with:

```
python manage.py runserver 0.0.0.0:8000
```

(In local dev compose, `runserver` is used instead of gunicorn so you get live reload on code changes.)

The `depends_on: db` setting ensures Postgres is started before the web container, though you may still need to wait a second for Postgres to finish initializing before running migrations.

### 5. The app is accessible at http://localhost:8000

Both services share a Docker network, so `web` can reach `db` at the hostname `db` (matching the service name in `docker-compose.yml`). The `DATABASE_URL` env var is set to `postgres://bts:bts@db:5432/bts`, which tells Django where to find Postgres.

### Running migrations

After the first `docker compose up --build`, run migrations in a separate terminal:

```bash
docker compose run web python manage.py migrate
```

This only needs to be done once (or whenever new migrations are added).

---

## Settings Split Explained

The original `config/settings.py` had everything in one file: a hardcoded `SECRET_KEY`, `DEBUG = True`, and an SQLite database path. That works for development but is wrong for production.

The settings are now split into three files:

```
config/settings/
├── __init__.py   # empty — makes this a package
├── base.py       # everything shared across all environments
├── dev.py        # imports base, adds dev-specific overrides
└── prod.py       # imports base, adds prod-specific overrides
```

**`base.py`** contains all the settings that never change: `INSTALLED_APPS`, `MIDDLEWARE`, `TEMPLATES`, `AUTH_PASSWORD_VALIDATORS`, etc. It reads `SECRET_KEY` from the environment using `django-environ` — no secret is ever hardcoded.

**`dev.py`** sets `DEBUG = True`, opens `ALLOWED_HOSTS` to everything (`['*']`), and uses `DATABASE_URL` from the environment (defaulting to SQLite if not set).

**`prod.py`** reads all sensitive values strictly from environment variables. `DEBUG` defaults to `False`. `ALLOWED_HOSTS` must be set explicitly. `SECURE_PROXY_SSL_HEADER` tells Django to trust the `X-Forwarded-Proto: https` header from Coolify's reverse proxy, so Django knows the request arrived over HTTPS.

The active settings module is selected via the `DJANGO_SETTINGS_MODULE` environment variable:

| Context | Value |
|---|---|
| Local `manage.py` commands | `config.settings.dev` (set as default in `manage.py`) |
| Docker local dev | `config.settings.dev` (set in `docker-compose.yml`) |
| Production / Coolify | `config.settings.prod` (set in `docker-compose.prod.yml`) |
| gunicorn (wsgi.py default) | `config.settings.prod` (set as default in `wsgi.py`) |

---

## Environment Variables

All configuration is passed via environment variables. Never hardcode secrets in code.

| Variable | Required in prod | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Django's cryptographic signing key. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DATABASE_URL` | Yes | Full database connection string. Format: `postgres://user:password@host:5432/dbname` |
| `ALLOWED_HOSTS` | Yes | Comma-separated list of allowed hostnames, e.g. `yourdomain.com,www.yourdomain.com` |
| `DEBUG` | No | Defaults to `False` in prod settings. Set to `True` only for debugging on a live server (not recommended). |
| `DJANGO_SETTINGS_MODULE` | Yes | Which settings file to use. Set to `config.settings.prod` on the server. |

For local development, these are defined in `.env` (gitignored). For production, Coolify injects them via its environment variables UI.

### `.env.example`

A committed template showing which variables are needed:

```
SECRET_KEY=your-secret-key-here
DEBUG=False
DATABASE_URL=postgres://user:password@db:5432/bts
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

Copy this to `.env` and fill in real values for local dev. Never commit `.env`.

---

## Local Dev Workflow

```bash
# First time setup
docker compose up --build
docker compose run web python manage.py migrate

# Normal startup after that
docker compose up

# Run management commands inside the container
docker compose run web python manage.py shell
docker compose run web python manage.py generate_predictions --date 2026-04-03

# Stop everything
docker compose down

# Wipe the database volume and start fresh
docker compose down -v
docker compose up --build
docker compose run web python manage.py migrate
```

Code changes (in `predictor/`, `config/`, templates) take effect immediately because the project directory is mounted into the container as a volume — no rebuild needed.

A full rebuild (`--build`) is only needed when you change `requirements.txt` or `Dockerfile`.

---

## Production Deployment (Coolify + Hetzner)

The production stack uses:

- **Hetzner CAX11** (arm64, €3.29/mo) as the VPS
- **Coolify** (self-hosted on the same VPS) as the CD platform
- **`docker-compose.prod.yml`** as the compose file Coolify runs
- **Coolify-managed Postgres** — Coolify provisions its own Postgres container and injects `DATABASE_URL` automatically

### Deployment flow

```
git push → GitHub webhook → Coolify picks up changes
→ docker compose -f docker-compose.prod.yml up --build
→ migrate + gunicorn starts
→ app live at https://yourdomain.com
```

### What `docker-compose.prod.yml` does differently from dev

- Uses `config.settings.prod` (strict security settings)
- Runs `migrate` before gunicorn starts, so schema is always up to date after a deploy
- Uses gunicorn (multi-worker) instead of `runserver`
- Does not mount the source directory as a volume — the image is the source of truth
- Does not include a `db` service — Coolify manages Postgres separately

### First deploy checklist

1. Set up a Hetzner VPS and install Coolify (see [Coolify docs](https://coolify.io/docs))
2. Add the GitHub repo to Coolify
3. Set the compose file to `docker-compose.prod.yml`
4. Configure environment variables in Coolify's UI: `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE`
5. Push to `main` — Coolify handles the rest

---

## File Reference

### `Dockerfile`

Builds the production app image. Key decisions:

- `python:3.11-slim` — minimal base image, no unnecessary system packages
- `gcc` and `libpq-dev` are required to compile `psycopg2-binary` on Linux
- `collectstatic` runs at build time so static files are baked into the image — no separate step needed at runtime
- A dummy `SECRET_KEY` is used during build because Django's settings load at import time and require it, even though no actual secret is needed for collecting static files

### `docker-compose.yml`

Local development compose file. Uses `runserver` (not gunicorn) for live reload. Mounts the project directory as a volume so code changes are reflected instantly without rebuilding.

### `docker-compose.prod.yml`

Production compose file for Coolify. Runs migrations then starts gunicorn. All secrets come from environment variables injected by Coolify at runtime.

### `.dockerignore`

Excludes files that should not be copied into the Docker image:

- `venv/` — the image installs its own dependencies via pip
- `.env` — secrets must come from runtime env vars, not baked into the image
- `db.sqlite3` — the image uses Postgres
- `__pycache__/`, `*.pyc` — compiled Python bytecode from the host
- `.git/` — version control history is not needed in the image
- `staticfiles/` — regenerated by `collectstatic` during the build
