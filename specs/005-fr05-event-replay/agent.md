# Agent Brief: FR5 Event Replay

## Owner role
**Data & ML**

## Mission
Replay three real monsoon windows (Sindh 2022, Buner 2025, and the normal 2021 Sindh season) day by day through the **real** daily pipeline, with a simulated clock and cached historical inputs, so judges watch tiers fire and money move as it would have. Every replay row is isolated by `scenario_id`. The same replays, run headless, are the NFR3 acceptance backtests: Sindh 2022 and Buner 2025 must trigger, the 2021 control must not.

## Scope
**In (MVP)**
- `ReplayScenario` definitions in `data/replay/scenarios.yaml` (exact windows, districts, reference onsets, lead requirements, demo fund sizes).
- Cache orchestration and integrity: `scripts/replay/build_cache.py` calls FR1, FR2, FR3 (and FR21 D1) cache writers and writes `data/replay/<key>/manifest.json`.
- `ReplayClock`; `ReplayEngine` with start, step, seek, reset, state, timeline; autoplay (play/pause) for the 3-minute demo.
- Scenario set-up: scenario-scoped funds and tiers (via FR6), mode snapshot (FR4), 30-day spin-up ingest.
- Simulated DC approval (`channel=REPLAY_SIM`, `simulated=true`) or a real WhatsApp/dashboard approval during the live demo.
- Reset with a fresh audit chain (`run_no` + 1, FR20) and deletion of the scenario's operational rows.
- Backtest evaluator + `tests/backtest/` (NFR3) + `scripts/replay/run_backtest.py` report.

**Out**
- Source-specific replay readers (each source FR owns one: FR1 `nasa_power/*.json`, FR2 `imerg_daily.csv`, FR3 `forecast_rain.parquet`, FR21 precomputed S1 maps).
- Any business logic of the pipeline steps (FR3, FR7–FR12, FR14, FR17).
- Frontend slider (consumes `timeline`).
- More scenarios (roadmap: e.g. a flash-mode control year).

## Read first
- `specs/_authoring-guide.md` (FR5 facts, shared rules 2 and 3), `.specify/memory/constitution.md` (II, III, VIII, X, XI)
- `specs/000-foundation/data-model.md` §9 (ReplayScenario), every table's `scenario_id`; `specs/000-foundation/plan.md` (`Clock`, `X-Scenario-Id`)
- `specs/003-fr03-flood-risk-score/clarify.md` (forecast provenance), `specs/004-fr04-flood-modes/plan.md` (mode snapshot)
- `specs/020-fr20-audit-log/clarify.md` (chain per `run_no`), `specs/001-fr01-nasa-power-ingestion/plan.md` (replay client, lag days), `specs/002-fr02-earth-engine-ingestion/agent.md` (IMERG cache), `specs/007-fr07-auto-tier-release/clarify.md` (seek backwards = reset), `specs/014-fr14-whatsapp-alerts/clarify.md` (allowlist, dedupe on reset)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | Foundation | `set_clock/get_clock`, `use_scenario`, pipeline runner, scheduler `run_now` |
| Depends on | FR1, FR2, FR3 | Replay readers + cache writers; ingest and forecast steps |
| Depends on | FR4 | `snapshot_modes` |
| Depends on | FR6 | `create_fund_with_tiers` for scenario funds |
| Depends on | FR7, FR8 | Tier firing; `Approval` decide API for `REPLAY_SIM` |
| Depends on | FR14, FR17 | Alert and payment dispatch jobs drained per step |
| Depends on | FR20 | `run_no` chains |
| Blocks | Demo | The 3-minute live demo is a replay |
| Blocks | NFR3 sign-off | Backtests are the acceptance tests |

## Interfaces
**Consumes**: `get_clock()/set_clock()`, `use_scenario(sid)`, `run_daily_pipeline(db, clock)` (foundation pipeline runner, executes `register_step` steps in order), `all_ingestors()` + `Ingestor.ingest(db, start=, end=, district_ids=)` (FR1), `create_fund_with_tiers(db, district_id, scenario_id, …)` (FR6), `snapshot_modes(db, ids)` (FR4), `ApprovalService.decide(db, approval_id, decision=, channel=, actor=, reason=, simulated=)` (FR8; align name with FR8 plan), scheduler `run_now(job_id)`, `AuditService.record`.
**Provides**: `ReplayClock`, `ReplayEngine.start/step/seek/reset/state/timeline`, `evaluate_backtest(db, scenario) -> BacktestReport`, `ensure_scenarios(db)`, `current_replay()` helper for other FRs (returns key, sim date, run_no, approval mode).

## Files you own
```text
app/services/replay/__init__.py
app/services/replay/clock.py
app/services/replay/scenarios.py
app/services/replay/cache.py
app/services/replay/engine.py
app/services/replay/approvals.py
app/services/replay/reset.py
app/services/replay/backtest.py
app/services/replay/autoplay.py
app/schemas/replay.py
app/api/v1/replay.py
alembic/versions/0005_fr05_replay.py
data/replay/scenarios.yaml
data/replay/<key>/manifest.json            # generated, committed
scripts/replay/build_cache.py
scripts/replay/run_backtest.py
tests/unit/replay/test_replay_clock.py
tests/unit/replay/test_backtest_eval.py
tests/unit/replay/test_scenarios_yaml.py
tests/integration/replay/test_replay_engine.py
tests/integration/replay/test_replay_reset.py
tests/contract/test_replay_api.py
tests/fixtures/replay/mini/                 # 1 district, 5 days, for fast CI
tests/backtest/conftest.py
tests/backtest/test_nfr3_backtests.py
tests/backtest/test_replay_isolation.py
```

## Guardrails
| Principle | How it applies |
|---|---|
| II Human authority | Action never skips the `Approval` row: `REPLAY_SIM` approvals are real rows with `simulated=true`; the backtest asserts every Action release has one |
| III Traceability | Nothing in `audit_log` is deleted; reset starts a new chain (`run_no`) |
| VIII Honest prototype | Every replay response carries `simulated: true`, the scenario key and forecast provenance; proxies are labelled |
| X Simplicity | Same process, same pipeline, same DB; isolation by `scenario_id` + contextvars |
| XI Tests | Backtests are acceptance tests; isolation test proves live data untouched |
| IX Config over code | Windows, districts, onsets, fund sizes in YAML |

## Definition of Done
- [ ] `pytest -m backtest` passes for all three scenarios with committed caches and artifacts.
- [ ] Isolation test: live row counts and `DataSourceStatus` unchanged after all three replays.
- [ ] Start → step → seek → reset → start again works; old audit chain still verifies (FR20 `?run_no=`).
- [ ] Every Action release in a replay has an `Approval` (REPLAY_SIM or real).
- [ ] Full `sindh-2022` run (41 days, 5 districts) completes in < 4 min on a laptop; one step < 6 s.
- [ ] `manifest.json` committed for each scenario; engine refuses to start on a hash mismatch.
- [ ] Demo dry run done with autoplay and one live WhatsApp approval.

## Hand-off notes
- **FR8**: in a scenario, do not start the 2-hour escalation timer when `approval_mode=AUTO_SIM`; in `LIVE_WHATSAPP` mode keep it, but escalation is wall-clock and will not fire on stage. When the DC's WhatsApp reply arrives, process it inside `use_scenario(approval.scenario)`.
- **FR14**: replay alerts follow your allowlist; FR5 deletes the scenario's alert rows on reset, so your `dedupe_key` does not need `run_no`.
- **FR7**: FR5 never sends an older date to `evaluate`; seek backwards always resets first.
- **FR17 / FR14**: expose your dispatch jobs' ids; FR5 drains them after each simulated day (`REPLAY_DRAIN_JOBS`).
- **Frontend**: drive the slider from `GET /replay/scenarios/{key}/timeline` and pass `X-Scenario-Id: <scenario_id>` to every other read.
