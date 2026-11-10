# Implementation Plan: 000 Foundation (shared backend infrastructure)

**Branch**: `000-foundation` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: the constitution (v1.0.0) and the project document (NFR6, NFR9, NFR11, NFR13)

## Summary

This plan stands up the single FastAPI service, the PostgreSQL + PostGIS database, configuration, authentication with roles, PII encryption, the scheduler, feature flags, replay scoping, seed data and the cross-cutting **service interfaces** that every FR plan calls. No FR business logic lives here. This plan defines the seams.

## Technical Context

| | |
|---|---|
| **Language/Version** | Python 3.12 |
| **Primary Dependencies** | fastapi, uvicorn, pydantic v2, pydantic-settings, sqlalchemy 2.x, alembic, psycopg[binary], geoalchemy2, apscheduler, python-jose[cryptography], passlib[bcrypt], cryptography, httpx, structlog |
| **Storage** | PostgreSQL 16 + PostGIS 3.4 (docker-compose for local) |
| **Testing** | pytest, pytest-asyncio, httpx, factory-boy, a Postgres test database (`docker compose -f docker-compose.test.yml`) |
| **Target Platform** | Linux container (Render, Railway or a VM) and Windows/macOS local dev |
| **Project Type** | web-service (REST API + scheduled jobs) |
| **Performance Goals** | Dashboard reads p95 < 500 ms. The daily pipeline covers all districts in < 15 min |
| **Constraints** | No floats for money. UTC timestamps. Idempotent writes for jobs and webhooks |
| **Scale/Scope** | Pilot: Sindh (30 districts) + KP (38 districts), about 260 tehsils, up to 50k synthetic beneficiaries |

## Constitution Check

| Principle | How the foundation satisfies it | ✅ |
|---|---|---|
| II Human authority | `require_role()` dependency. `TriggerTier.requires_approval` is not writable via any schema | ✅ |
| III Traceability | Defines the `AuditService.record()` interface (implemented by FR20). The repository base class requires an `audit` argument on mutating calls | ✅ |
| VI Privacy | `EncryptedString` SQLAlchemy type (Fernet). The `DonorScope` response base class forbids beneficiary fields | ✅ |
| VII Degrade gracefully | `DataSourceStatus` table plus `StaleAware` response mixin (`is_stale`, `stale_since`) | ✅ |
| VIII Honest prototype | `SimulatedMixin` adds `simulated: bool` to models and schemas. Response middleware adds the header `X-Simulated-Data: true` when any simulated data is present | ✅ |
| IX Config over code | Seeds in `data/seeds/*.yaml`. Fund defaults in settings, not in code | ✅ |
| X Simplicity | One service, in-process APScheduler, no message broker | ✅ |
| XI Tests | Test DB fixture, factories and an auth helper are ready for every FR | ✅ |

## Project Structure

```text
app/
├── main.py                     # create_app(): routers, middleware, exception handlers, scheduler start
├── core/
│   ├── config.py               # Settings (pydantic-settings): DATABASE_URL, JWT_SECRET, FERNET_KEY, TWILIO_*, GEE_*, FLAGS
│   ├── db.py                   # engine, SessionLocal, get_db dependency, Base, TimestampMixin, ScenarioMixin
│   ├── security.py             # hash/verify password, create/decode JWT, get_current_user, require_role(*roles)
│   ├── crypto.py               # EncryptedString type, phone normalisation + phone_hash
│   ├── scenario.py             # ScenarioContext: contextvar holding scenario_id (None = live), query helper
│   ├── flags.py                # feature flags: FR13_LLM, FR16_FALLBACK, FR21_LIVE_VERIFY, FR22_OUTCOME
│   ├── clock.py                # Clock interface: SystemClock / ReplayClock (FR5 injects)
│   ├── scheduler.py            # APScheduler wrapper: register_job(), run_now()
│   ├── logging.py              # structlog JSON config, request id middleware
│   └── errors.py               # DomainError, NotFound, Conflict(409), Forbidden → JSON problem responses
├── models/                     # one module per aggregate, matching data-model.md sections
│   ├── geography.py  ingestion.py  forecasting.py  funds.py  beneficiaries.py
│   ├── payments.py   messaging.py  audit.py        users.py  replay.py
├── schemas/common.py           # Page[T], Money, StaleAware, SimulatedMixin, DonorScope
├── api/v1/
│   ├── __init__.py             # api_router aggregating all feature routers
│   ├── auth.py                 # POST /auth/token, GET /auth/me
│   ├── districts.py            # GET /districts, GET /districts/{id}, GET /districts/{id}/tehsils (GeoJSON)
│   └── health.py               # GET /health, GET /health/data-sources
├── services/interfaces.py      # Protocols listed below (implemented by the owning FR)
└── jobs/daily_pipeline.py      # skeleton: ingest → forecast → triggers (steps registered by FRs)
alembic/versions/0001_foundation.py
data/seeds/{districts.yaml, users.yaml, funds.yaml}
data/boundaries/                # OCHA COD-AB adm2/adm3 GeoJSON for Sindh + KP
scripts/seed.py                 # loads boundaries, users and default funds
tests/conftest.py               # db fixture (transaction rollback), client, auth_headers(role)
docker-compose.yml              # postgis/postgis:16-3.4 + api
.env.example
pyproject.toml                  # deps, ruff, mypy, pytest config
```

**Structure decision**: one project at the repository root, laid out exactly as in the constitution.

## Shared Service Interfaces (`app/services/interfaces.py`)

Every FR codes against these Protocols. The foundation ships no-op or in-memory stubs so FRs can be built in parallel. The owning FR replaces each stub.

| Interface | Signature | Owner FR | Foundation stub |
|---|---|---|---|
| `AuditService` | `record(db, *, event_type: str, entity_type: str, entity_id: UUID, actor: str, payload: dict) -> AuditLog` | FR20 | Appends a row without a hash |
| `AlertService` | `enqueue(db, *, recipient, purpose, template_key, context: dict, related: dict) -> Alert` | FR14 | Logs only |
| `ExplainService` | `explain_risk(risk_inputs) -> (en, ur)`, `explain_allocation(alloc_inputs) -> (en, ur)` | FR3 owns `explain_risk` templates; FR11 owns `explain_allocation` templates; FR13 adds the optional LLM rewrite | Returns `""` |
| `PaymentRail` | `send(payment) -> RailResult(status, txn_id, failure_reason)` | FR17 | Always `PAID` |
| `TriggerEngine` | `evaluate(db, district_id, risk_index) -> list[FundRelease]` | FR7 | No-op |
| `AllocationService` | `allocate(db, fund_release) -> list[Allocation]` | FR11 + FR12 | No-op |
| `Clock` | `today() -> date`, `now() -> datetime` | Foundation; FR5 swaps in `ReplayClock` | `SystemClock` |

## API Conventions (all FRs)

- Base path is `/api/v1`. JSON bodies use snake_case. Money is serialised as a string decimal, e.g. `"15000.00"`.
- Lists return `{"items": [...], "total": n, "page": p, "page_size": s}`.
- Errors use RFC 7807 problem JSON, `{"type","title","status","detail","code"}`, with codes such as `TIER_PCT_SUM_INVALID` and `INVALID_TRANSITION`.
- Auth uses `Authorization: Bearer <jwt>`. The role comes from the token. A DC is automatically scoped to `district_id`.
- The `X-Scenario-Id` header (optional) scopes reads to a replay scenario. Without it, reads return live data.
- Webhooks live under `/api/v1/webhooks/*`. They use Twilio signature validation, not JWT.
- OpenAPI tags match the FR areas: Data, Forecasting, Triggers, Beneficiaries, Communication, Payments, Reporting.

## Seed Users (demo)

| email | role | district |
|---|---|---|
| officer@pdma-sindh.demo | PDMA_OFFICER | — (province: Sindh) |
| dc.dadu@sindh.demo | DC | Dadu |
| dc.buner@kp.demo | DC | Buner |
| donor@fund.demo | DONOR | — (linked to the Dadu and Buner funds) |
| admin@aab.demo | ADMIN | — |

## Complexity Tracking

None.
