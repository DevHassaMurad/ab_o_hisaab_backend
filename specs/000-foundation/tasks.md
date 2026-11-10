---
description: "Task list for 000 Foundation"
---

# Tasks: 000 Foundation

**Input**: `specs/000-foundation/` (plan.md, spec.md, data-model.md)

**Tests**: Required for auth/RBAC, encryption, scenario scoping and simulated labelling (Principle XI and Principle VI).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Create the repo layout from plan.md, plus `pyproject.toml` (deps, ruff, mypy, pytest config) at the repo root
- [ ] T002 [P] Create `docker-compose.yml` (postgis/postgis:16-3.4 + api) and `docker-compose.test.yml` (test database on port 5433)
- [ ] T003 [P] Create `.env.example` with DATABASE_URL, JWT_SECRET, JWT_TTL_MIN=60, FERNET_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, GEE_SERVICE_ACCOUNT, GEE_KEY_FILE, OPEN_METEO_BASE, ANTHROPIC_API_KEY, FLAGS
- [ ] T004 [P] Write `README.md` covering setup, run, seed, test and the role logins
- [ ] T005 Implement `app/core/config.py` (pydantic-settings `Settings`, which fails fast if FERNET_KEY is missing or invalid)
- [ ] T006 [P] Implement `app/core/logging.py` (structlog JSON + request-id middleware)

## Phase 2: Foundational

- [ ] T007 Implement `app/core/db.py`: engine, `SessionLocal`, `get_db`, `Base`, `TimestampMixin`, `ScenarioMixin`, `SimulatedMixin`
- [ ] T008 Implement `app/core/errors.py`: `DomainError` hierarchy plus RFC 7807 exception handlers
- [ ] T009 [P] Implement `app/core/clock.py`: the `Clock` protocol, `SystemClock`, and the `get_clock()`/`set_clock()` contextvar
- [ ] T010 [P] Implement `app/core/scenario.py`: `ScenarioContext` contextvar, `get_scenario` dependency (reads `X-Scenario-Id`), `scoped(query, model)` helper
- [ ] T011 [P] Implement `app/core/flags.py`: `is_enabled(flag)` with FR13_LLM, FR16_FALLBACK, FR21_LIVE_VERIFY, FR22_OUTCOME (default off)
- [ ] T012 [P] Implement `app/core/scheduler.py`: APScheduler `BackgroundScheduler` wrapper with `register_job(id, func, trigger)` and `run_now(id)`
- [ ] T013 Implement all models in `app/models/{geography,ingestion,forecasting,funds,beneficiaries,payments,messaging,audit,users,replay}.py` exactly as in data-model.md
- [ ] T014 Generate and hand-check `alembic/versions/0001_foundation.py`: `CREATE EXTENSION postgis`, all tables, enums, unique and partial-unique indexes (one OPEN TriggerEvent per district per scenario)
- [ ] T015 [P] Implement `app/schemas/common.py`: `Money`, `Page[T]`, `StaleAware`, `SimulatedMixin`, `DonorScope` (PII deny-list validator)
- [ ] T016 Implement `app/services/interfaces.py` (the Protocols in plan.md) and `app/services/stubs.py` (stub implementations)
- [ ] T017 Implement `app/core/deps.py`: providers `get_audit()`, `get_alerts()`, `get_explain()`, `get_rail()`, `get_trigger_engine()`, `get_allocator()`, which return stubs until each FR registers its real implementation
- [ ] T018 [P] Create `tests/conftest.py` (DB fixture with per-test transaction rollback, `client`, `auth_headers(role, district=None)`) and `tests/factories.py`

**Checkpoint**: Models migrate, and the test harness runs an empty test.

## Phase 3: User Story 1 - Roles and sign-in (P1) 🎯 MVP

- [ ] T019 [P] [US1] Write tests in `tests/integration/test_auth.py`: login success and failure, an expired token, role mismatch, and the DC district scope
- [ ] T020 [P] [US1] Write a test in `tests/contract/test_route_roles.py`: every route except health, auth/token and webhooks declares `require_role`
- [ ] T021 [US1] Implement `app/core/security.py`: bcrypt hashing, JWT encode/decode, `get_current_user`, `require_role(*roles)`, `assert_district_access(user, district_id)`
- [ ] T022 [US1] Implement `app/api/v1/auth.py`: `POST /auth/token` and `GET /auth/me`
- [ ] T023 [US1] Create `data/seeds/users.yaml` (the 5 demo users from plan.md) and the users loader in `scripts/seed.py`

## Phase 4: User Story 2 - Districts and tehsils as configuration (P1)

- [ ] T024 [P] [US2] Write tests in `tests/integration/test_seed.py`: idempotent seeding, default fund and tiers created, inactive district skipped
- [ ] T025 [US2] Add OCHA COD-AB adm2/adm3 GeoJSON for Sindh + KP to `data/boundaries/` (simplified to about 100 m tolerance for API speed, keeping the full geometry for Earth Engine)
- [ ] T026 [US2] Create `data/seeds/districts.yaml` (pcode, flood_mode, is_active, vulnerability_weight placeholder) and `data/seeds/funds.yaml` (defaults)
- [ ] T027 [US2] Implement the boundary, district and fund loaders in `scripts/seed.py` (upsert by pcode)
- [ ] T028 [US2] Implement `app/api/v1/districts.py`: `GET /districts`, `GET /districts/{id}`, `GET /districts/{id}/tehsils?format=geojson`

## Phase 5: User Story 3 - PII protected at rest (P1)

- [ ] T029 [P] [US3] Write tests in `tests/unit/core/test_crypto.py`: round-trip, ciphertext in raw SQL, phone normalisation variants giving the same hash
- [ ] T030 [US3] Implement `app/core/crypto.py`: `EncryptedString` TypeDecorator, `normalise_pk_phone()` and `phone_hash()`

## Phase 6: User Story 4 - Replay isolation (P2)

- [ ] T031 [P] [US4] Write tests in `tests/integration/test_scenario_scope.py`: a live read excludes replay rows and a replay read excludes live rows
- [ ] T032 [US4] Wire `ScenarioMixin` defaults (the insert takes `scenario_id` from the context) and apply `scoped()` in the base repository helpers

## Phase 7: User Story 5 - Simulated and stale labelling (P2)

- [ ] T033 [P] [US5] Write tests in `tests/integration/test_labels.py`: the `X-Simulated-Data` header, and `/health/data-sources` staleness
- [ ] T034 [US5] Implement the simulated-header middleware in `app/main.py` and `app/api/v1/health.py` (`GET /health`, `GET /health/data-sources`)

## Phase 8: Polish

- [ ] T035 Write `app/jobs/daily_pipeline.py`, a skeleton with step registry `register_step(order, name, func)`, plus admin `POST /api/v1/admin/pipeline/run`
- [ ] T036 [P] Get `ruff` and `mypy --strict app/models app/services` clean
- [ ] T037 Run the quickstart from implement.md end to end on a clean machine

## Dependencies & Execution Order

- Setup (T001–T006) → Foundational (T007–T018) → US1 and US3 (they can run in parallel) → US2 → US4 → US5 → Polish.
- T013 blocks T014. T016 and T017 block every FR.

## Parallel opportunities

- T002, T003 and T004. Then T009, T010, T011, T012 and T015. Each story's test task runs in parallel with the other stories' test tasks.
