# Aab o Hisaab backend

FastAPI backend with authentication, flood-data ingestion, risk forecasting, fund
settings and historical replay modules. Databases run in Docker; Python runs on the
host. **MySQL is the default database. PostgreSQL/PostGIS is optional.**

The repository currently has two independent applications:

| Application | Entry point | Routes | Database | Migration configuration |
| --- | --- | --- | --- | --- |
| Default authentication backend | `main:app` | `/api/auth/*` | MySQL (`mysql`) | `alembic.ini` -> `migrations/`, through `artisan.py` |
| Flood/forecast/replay backend | `app.main:app` | `/api/v1/*` | PostgreSQL/PostGIS (`db`, `postgres` profile) | `alembic.app.ini` -> `alembic/` |

The flood modules use PostgreSQL-specific types, spatial features and SQL. Choosing
MySQL as the default does not convert those modules to MySQL. Keep the two schemas,
migration histories and tokens separate. Both API examples use **port 5800**; run
one at a time or choose another port for the second process.

## Prerequisites

- Python 3.12 or later.
- Docker Desktop running Linux containers, with Docker Compose v2 supporting `--wait`.
- Run these PowerShell commands from the backend directory.

## Quick start: default MySQL backend

For a fresh checkout:

```powershell
Copy-Item .env-example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d --wait mysql
.\.venv\Scripts\python.exe artisan.py migrate
.\.venv\Scripts\python.exe main.py
```

If `.env` or `.venv` already exists, keep it and merge missing settings from the
example instead of overwriting it. Replace the sample JWT secrets/passwords before
sharing an environment. `.env.example` is an identical compatibility copy of
`.env-example`; the actual `.env` is ignored by Git.

- Swagger UI: <http://127.0.0.1:5800/docs>
- Login: `POST /api/auth/authenticate` (JSON: `email`, `user_name`, `password`).
- Protected route: `GET /api/auth/protected` with a Bearer token.
- `python main.py` reads `HOST` and `PORT` from `.env` and starts Uvicorn.

MySQL publishes **127.0.0.1:3307** -> container port **3306**, avoiding conflicts
with a local MySQL server on 3306. The persistent named volume is `mysqldata`.
`docker compose up -d --wait` also starts just MySQL by default.

Docker credentials and the backend connection must match:

| Docker setting | Backend setting | Example |
| --- | --- | --- |
| `MYSQL_DATABASE` | `DATABASE_NAME` | `aab_auth` |
| `MYSQL_USER` | `DATABASE_USER` | `aab_auth` |
| `MYSQL_PASSWORD` | `DATABASE_PASSWORD` | Same local password |
| `MYSQL_PORT` | `DATABASE_PORT` | `3307` |
| Host loopback | `DATABASE_HOST` | `127.0.0.1` |
| MySQL engine | `DATABASE_DIALECT` | `mysql` |

`MYSQL_ROOT_PASSWORD` is only for database administration. The API uses the separate
application user. `PyMySQL[rsa]` provides MySQL 8 authentication support.

Starting Docker creates an empty database, not a copy of your existing local data.
Moving existing records requires an export/import. An existing local configuration
backup, if created during setup, stays ignored as `.env.before-docker`.

## Original authentication migrations

Password recovery, six-digit verification, email confirmation and SMTP setup:
[authentication flow](docs/authentication.md). After applying revision 0003, existing
accounts must confirm their email before signing in. Auth logic remains in the
existing `Authentication` controller.

```powershell
.\.venv\Scripts\python.exe artisan.py migrate:status
.\.venv\Scripts\python.exe artisan.py migrate:rollback
.\.venv\Scripts\python.exe artisan.py migrate:rollback --step 2
.\.venv\Scripts\python.exe artisan.py make:migration add_phone_to_users
```

If adopting an existing original `users` table with `id`, `name`, `email`, `password`,
run `artisan.py migrate:baseline` before the first `migrate`. A new Docker database
has no tables, so it does not need baseline.

The schema contains `users` and `roles`, with optional `users.image`, default
`role_id=0`, nullable `district_id` and `is_active`. Schema migrations create role
reference data; demo accounts are optional. Seed users using `artisan.py db:seed`
or `artisan.py migrate --seed` after configuring the seed password and actual
Dadu/Buner district UUIDs. Full details: [migrations/README.md](migrations/README.md).

`--step` counts revisions. Rolling back removes the columns/tables defined by each
revision, including their data. `migrate:reset` rolls everything back and drops the
migrated tables; use it only when intentionally discarding that schema/data.

## Optional PostgreSQL/PostGIS backend

Install the application dependencies declared in `pyproject.toml` and enable its
Docker profile:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
docker compose --profile postgres up -d --wait db
.\.venv\Scripts\python.exe -m alembic -c alembic.app.ini upgrade head
```

Set `DATABASE_URL` to match `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and
`POSTGRES_PORT`. This is separate from the original `DATABASE_*` MySQL settings.
The example publishes **127.0.0.1:5432** and uses the `pgdata` named volume.
`POSTGRES_TEST_DB` is a separate disposable test database. First initialization
creates it and enables PostGIS in both databases.

The existing PostGIS image tag is retained. Do not change a populated volume's
PostgreSQL/PostGIS version without planning its database upgrade.

Optionally seed geography, demo users, modes, funds and replay scenarios:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed
```

Set `SEED_USER_PASSWORD` first; the existing loader otherwise defaults to `demo`.
Re-running the loader updates seed configuration but retains existing passwords.
Demo emails/roles are listed in `data/seeds/users.yaml`.

Start this API after stopping the original API on port 5800:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5800 --reload
```

Swagger: <http://127.0.0.1:5800/docs>; health: `/api/v1/health`;
login: `POST /api/v1/auth/token` with form fields `username` (email) and `password`.
This app exposes ingestion, forecasting/modes, fund settings and replay endpoints.
For the Uvicorn CLI, `--port` controls the port; it does not automatically use `PORT`.

```powershell
.\.venv\Scripts\python.exe -m alembic -c alembic.app.ini current
.\.venv\Scripts\python.exe -m alembic -c alembic.app.ini history
.\.venv\Scripts\python.exe -m alembic -c alembic.app.ini revision --autogenerate -m "describe_change"
.\.venv\Scripts\python.exe -m alembic -c alembic.app.ini downgrade -1
```

Always review generated upgrade/downgrade code. Do not use `artisan.py` or the
default `alembic.ini` against this PostgreSQL schema.

## Environment settings

`.env-example` groups both database connections, JWT settings, optional demo seeds,
NASA POWER/context data, Earth Engine, forecasting and replay settings. Defaults for
other advanced settings live in `app/core/config.py`, `data/seeds/` and `ml/config/modes/`.

- `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES`: original authentication app.
- `JWT_SECRET`, `JWT_TTL_MIN`: PostgreSQL application authentication.
- `TEST_DATABASE_URL`: disposable PostgreSQL test database, never the application DB.
- `SCHEDULER_ENABLED`, `PIPELINE_CRON`: scheduled jobs (disabled by default).
- `GEE_SERVICE_ACCOUNT`, `GEE_KEY_FILE`, `GEE_PROJECT`: live Earth Engine integrations.
- `REPLAY_CACHE_ROOT`, `REPLAY_DEFINITIONS`: cached data and scenario configuration.
- `FORECAST_ARTIFACT_DIR`, `FORECAST_MODES_DIR`: local model artifacts and mode configuration.

Keep connection URLs in sync with Docker credentials/ports. URL-encode special
characters in URL passwords; the original auth app's separate `DATABASE_PASSWORD`
value stays unencoded. Environment files and service-account files belong outside Git.

## Tests and package management

```powershell
# Original migration lifecycle: temporary SQLite, no running database needed.
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_migrations.py -v
# App unit tests (install .[dev] first).
.\.venv\Scripts\python.exe -m pytest tests/unit
# Default full selection; DB tests need the PostgreSQL test database.
.\.venv\Scripts\python.exe -m pytest
```

The PostgreSQL fixture loads `.env`, uses `alembic.app.ini` and **drops/recreates the
public schema in TEST_DATABASE_URL**. Always point it at the separate test database.
Earth Engine, live-network and full backtest checks are excluded by the default
pytest marker expression in `pyproject.toml`.

`requirements.txt` tracks the original auth dependencies. Its helper records only
explicitly requested packages, without freezing unrelated global packages:

```powershell
.\packages.ps1 install package-name
.\packages.ps1 install --upgrade package-name
.\packages.ps1 uninstall package-name
.\packages.ps1
```

The last command refreshes already tracked versions. Plain pip installs bypass this
helper. `pyproject.toml` declares the PostgreSQL application's dependencies; optional
extras are `.[dev]`, `.[ml]` and `.[ee]`. Install them using the `.venv` Python and
update that file when adding application dependencies.

## Data, replay and ML scripts

| Module | Purpose |
| --- | --- |
| `scripts.seed` | Geography, users, modes, funds, scenarios and terrain seeds |
| `scripts.ingest_nasa_power` | Historical backfill and POWER replay-cache generation |
| `scripts.cache_replay_imerg` | Earth Engine IMERG replay-cache generation |
| `scripts.compute_tehsil_terrain` | Earth Engine terrain seed generation |
| `scripts.ee_smoke_test` | Optional live Earth Engine connectivity check |
| `scripts.replay.build_cache` | Replay-cache builder and manifest generation |
| `scripts.replay_scores` | Scenario scoring inside a rolled-back transaction |
| `ml.threshold_report` | Advisory threshold report; never changes trigger settings |

Examples (cache downloads need internet; scoring needs migrated/seeded PostgreSQL):

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_nasa_power --all-scenarios --to-cache
.\.venv\Scripts\python.exe -m scripts.replay.build_cache --all --manifest-only
.\.venv\Scripts\python.exe -m scripts.replay_scores --scenario buner-2025
```

## Repository structure

```text
main.py                      Default auth app and env-based Uvicorn runner
config/                      Original database/JWT configuration
controller/                  Original authentication controller
middlewares/                 Original bearer-auth dependency
model/                       Original User/Role SQLAlchemy models
routes/                      Original auth routes
migrations/                  Original revisions, optional users seeder, migration docs
alembic.ini, artisan.py       Original auth migration config and Laravel-style CLI
app/                         PostgreSQL/PostGIS flood application
  api/v1/                    Auth, health, ingestion, forecasting, funds, replay routes
  core/                      Settings, DB, JWT/roles, errors, scheduler, clocks
  integrations/              NASA POWER, Earth Engine, weather, World Bank clients
  jobs/                      Daily pipeline orchestration
  models/                    Geography, climate, forecast, fund, user, replay tables
  schemas/                   API request/response schemas
  services/                  Ingestion, forecasting, triggers, explanations, replay
alembic/                     PostgreSQL application's migration revisions
alembic.app.ini              Explicit PostgreSQL application migration config
docker-compose.yml           Default MySQL and optional postgres profile
docker/initdb/               PostgreSQL first-boot test-database initialization
data/seeds/                  Versioned geography, users, modes, funds and context data
data/replay/                 Scenario definitions, versioned caches and manifests
ml/config/modes/             Slow-onset/flash forecasting configuration
ml/threshold_report.py       Advisory threshold calibration reporting
ml/artifacts/                Local trained artifacts (ignored)
reports/backtest/            Versioned scenario backtest reports
scripts/                     Seed, ingestion, satellite and replay utilities
tests/                       Unit, contract, integration, backtest and migration checks
specs/                       Existing design/specification documents
requirements.txt             Original auth dependency pins
pyproject.toml               App dependencies/extras, pytest and Ruff configuration
packages.ps1                 Original package install/remove/refresh helper
.env-example, .env.example   Matching sanitized configuration templates
.env                         Local settings/secrets (ignored)
.gitignore, .gitattributes    Ignore rules; Unix line endings for container scripts
```

## Database operations

```powershell
docker compose ps
docker compose logs --tail 100 mysql
docker compose --profile postgres logs --tail 100 db
docker compose --profile postgres stop
docker compose --profile postgres down
```

Normal `down` preserves named volumes. `down -v` deletes database volumes/data.
Initialization settings/scripts only apply to a new volume; changing a password in
`.env` does not update an existing database user. Update credentials through SQL or
restore into a new volume instead of deleting a database you need.

For a reused PostgreSQL volume missing its test database, confirm the test name is
different from the application DB, then run the idempotent initialization script:

```powershell
docker compose --profile postgres exec db sh /docker-entrypoint-initdb.d/01-test-db.sh
```

If a host database port is occupied, change both the published port and corresponding
connection setting/URL. API port 5800 is separate from database ports 3307/5432.
Host Python connects to `127.0.0.1`; a containerized client would use `mysql:3306` or
`db:5432`. The setup containers here run databases only.

`.gitignore` excludes env files/secrets, Python/test/editor caches, DB backups,
downloaded/intermediate data and trained artifacts. Seeds, test fixtures, replay
caches, configuration and checked-in backtest reports remain versioned. Ignoring
and untracking `.env` does not erase it from older Git commits; rotate previously
committed credentials before sharing that history.
