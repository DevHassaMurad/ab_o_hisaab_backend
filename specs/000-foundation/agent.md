# Agent Brief: 000 Foundation

## Owner role
Backend & alerts (lead). Satellite & GIS supplies the boundary files.

## Mission
Stand up the one service that every FR plugs into: the FastAPI app, Postgres + PostGIS, configuration, JWT auth with four roles, PII encryption, scenario scoping for replays, a swappable clock, the scheduler, feature flags, seed data and stub implementations of the shared service interfaces. After this, all 22 FRs can be built in parallel without stepping on each other.

## Scope
**In**
- Project scaffolding, `pyproject.toml`, `docker-compose.yml`, `.env.example`
- All SQLAlchemy models from `data-model.md` and migration `0001_foundation`
- Auth: `/auth/token`, `/auth/me`, `require_role()`, DC district scoping
- `EncryptedString`, phone normalisation and `phone_hash`
- `ScenarioContext`, `Clock` (`SystemClock`), feature flags, the APScheduler wrapper
- `services/interfaces.py` Protocols plus stub implementations
- Read-only geography endpoints (districts, tehsils as GeoJSON) and health endpoints
- Seed script: boundaries (Sindh + KP), demo users, default funds and tiers
- Test harness: DB fixture, client, `auth_headers(role)`, factories

**Out**
- Any FR business logic: ingestion, scoring, triggers, allocation, payments, messaging, the real audit hashing (FR20)

## Read first
- `.specify/memory/constitution.md`
- `specs/_authoring-guide.md`
- `specs/000-foundation/data-model.md`, `plan.md`, `spec.md`

## Depends on / Blocks
- **Depends on**: nothing.
- **Blocks**: every FR. Build this first, then FR20 (audit), because every other FR writes audit entries.

## Interfaces
- **Provides**:
  - Protocols: `AuditService`, `AlertService`, `ExplainService`, `PaymentRail`, `TriggerEngine`, `AllocationService`, `Clock`
  - Dependencies: `get_db`, `get_current_user`, `require_role`, `get_scenario`
- **Consumes**: none.

## Files you own
`app/main.py`, `app/core/*`, `app/models/*` (initial definitions), `app/schemas/common.py`, `app/api/v1/{__init__,auth,districts,health}.py`, `app/services/interfaces.py`, `app/services/stubs.py`, `app/jobs/daily_pipeline.py` (skeleton), `alembic/*`, `data/seeds/*`, `data/boundaries/*`, `scripts/seed.py`, `tests/conftest.py`, `tests/factories.py`, `tests/unit/core/*`, `docker-compose*.yml`, `pyproject.toml`, `.env.example`, `README.md`

## Guardrails
- **II**: `TriggerTier.requires_approval` exists in the model but in **no** writable schema.
- **III**: Mutating repository helpers take an `actor` and call `AuditService.record`. While FR20 isn't merged, the stub still writes rows, so tests can assert on audit entries.
- **VI**: The `DonorScope` base schema has a validator that fails if any field name is in the PII deny-list (`name`, `phone`, `cnic`, `wallet_id`, `beneficiary_id`).
- **VIII**: `SimulatedMixin` on models and schemas, plus the `X-Simulated-Data` header middleware.
- **IX**: No district name or threshold outside `data/seeds/` and `config.py` defaults.

## Definition of Done
- [ ] `docker compose up` gives a healthy API at `/api/v1/health` with PostGIS enabled
- [ ] `alembic upgrade head` creates every table in data-model.md
- [ ] `python scripts/seed.py` loads Sindh + KP districts and tehsils, 5 demo users, and a default fund and tiers for each active district
- [ ] Login works for each role, and each role is blocked from another role's endpoint (tests)
- [ ] Encrypted fields are unreadable in raw SQL, and their `phone_hash` lookup works (test)
- [ ] `X-Scenario-Id` scopes reads (test)
- [ ] `pytest` is green, and `ruff` and `mypy` are clean

## Hand-off notes
- FR20 replaces `StubAuditService` in `app/services/stubs.py` through the provider in `app/core/deps.py`. Every other FR replaces its own stub the same way.
- FR5 swaps `SystemClock` for `ReplayClock` through `app.core.clock.set_clock()` inside a scenario context.
