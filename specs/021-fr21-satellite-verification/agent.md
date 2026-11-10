# Agent Brief: FR21 Satellite Verification

## Owner role
**Satellite & GIS**

## Mission
Show, with real Sentinel-1 radar, where flooding actually happened. For the MVP (decision D1), precompute before/after flood maps for the replay events (Sindh 2022, Buner 2025), store them as `SatelliteFloodMap` rows with `precomputed=true, simulated=false`, and serve them through a read endpoint that respects the replay clock. As roadmap, behind `FR21_LIVE_VERIFY`, run a daily job that checks new Sentinel-1 images after a trigger and marks each `TriggerEvent` Confirmed, Not confirmed or Pending (UC02).

## Scope

**In (MVP, D1)**
- Offline precompute script: per scenario, per tehsil (and per district), before/after/flood-mask PNGs plus flooded km² and share, written to `data/replay/<key>/satellite/` with a `manifest.json`. Uses FR2's S1 primitives.
- Loader: manifest → `SatelliteFloodMap` rows scoped to the scenario, idempotent, no Earth Engine needed. Called by FR5 on scenario start/reset and by a CLI/admin endpoint.
- Read API: `GET /api/v1/satellite/flood-maps?tehsil=&district=&date=`, a district summary for the approval screen, and authenticated PNG serving. No lookahead in replay.
- Honest labels: `precomputed=true`, `simulated=false`, plus a plain "precomputed from real Sentinel-1 images" label in English and Urdu.
- Flag `FR21_LIVE_VERIFY` registered (default off) with a no-op job hook.

**Roadmap (behind `FR21_LIVE_VERIFY`)**
- Daily verification job (UC02 main flow, alt 1a, alt 4a): find eligible events, look for new S1 acquisitions, compute new water, write `SatelliteFloodMap` rows (`precomputed=false`), set `TriggerEvent.confirmation_status`, audit `FLOOD_VERIFIED`, flag Not confirmed / no-imagery events for PDMA review and notify the officer.
- Verification list endpoint and a manual "verify now" endpoint.
- Optional replay-mode verification from precomputed rows (clarify open item 3).

**Out**
- The Earth Engine algorithm itself (FR2). Trigger lifecycle (FR7). The approval screen (FR8 consumes the summary). Frontend rendering.
- Any effect on money: confirmation never releases, claws back or blocks funds (D4, Principle II).

## Read first
- `specs/_authoring-guide.md`, `.specify/memory/constitution.md` (D1, D4, Principles II, VII, VIII, IX)
- `specs/000-foundation/data-model.md` (`SatelliteFloodMap`, `TriggerEvent`, `FundRelease`, `ReplayScenario`, `DataSourceStatus`)
- `specs/000-foundation/plan.md` (API conventions, `X-Scenario-Id`, flags)
- `specs/002-fr02-earth-engine-ingestion/plan.md` and `implement.md` (S1 primitives)
- `specs/005-fr05-event-replay/` (scenario keys, windows, start/reset hooks) once written
- `specs/007-fr07-auto-tier-release/` (TriggerEvent / FundRelease lifecycle) and `specs/008-fr08-dc-action-approval/` (approval detail view)

## Depends on / Blocks

| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, `ScenarioContext`, `Clock`, flags, scheduler, roles |
| Depends on | FR2 (`002-fr02-earth-engine-ingestion`) | `s1_flood_change`, `flood_area_by_tehsil`, `thumbnail_png`, `s1_acquisitions`, `tehsil_feature_collection` |
| Depends on | FR5 (`005-fr05-event-replay`) | `ReplayScenario` rows; calling `load_flood_maps_for_scenario` on start/reset |
| Depends on | FR20 (`020-fr20-audit-log`) | `AuditService.record` |
| Depends on (roadmap) | FR7 (`007-fr07-auto-tier-release`) | `TriggerEvent` (with `confirmation_status=PENDING` on creation) and `FundRelease` tier times |
| Depends on (roadmap) | FR14 (`014-fr14-whatsapp-alerts`) | PDMA review notice |
| Blocks | FR8 (`008-fr08-dc-action-approval`) | "Satellite map (if any)" in the approval detail (UC07-2) |
| Blocks | FR19 (`019-fr19-donor-report`) | Optional aggregate "where flooding happened" panel |
| Blocks | Frontend | Before/after slider on the replay map |

## Interfaces

**Consumes**: FR2 gateway functions; `AuditService.record`; `AlertService.enqueue` (roadmap); `Clock`; `ScenarioContext`; `flags.is_enabled("FR21_LIVE_VERIFY")`; `scheduler.register_job`.

**Provides** (`app/services/satellite/`):
- `SatelliteService.list_maps(db, *, tehsil_id=None, district_id=None, as_of: date, scenario_id) -> list[SatelliteFloodMap]`
- `SatelliteService.district_summary(db, district_id, as_of, scenario_id) -> DistrictFloodSummary | None` (for FR8, FR19)
- `load_flood_maps_for_scenario(db, scenario_key) -> LoadResult` (for FR5; idempotent)
- `precompute_scenario(scenario_key, *, tehsils=None, dry_run=False) -> Manifest` (script only; needs EE)
- Roadmap: `FloodVerifier.verify_event(db, event, now) -> VerificationOutcome`, `run_verification_job(db, clock)`

## Files you own
```
app/services/satellite/__init__.py
app/services/satellite/flood_maps.py        # SatelliteService (read side)
app/services/satellite/loader.py            # manifest → rows
app/services/satellite/precompute.py        # EE → PNGs + manifest (script use)
app/services/satellite/verifier.py          # roadmap FloodVerifier
app/services/satellite/jobs.py              # register_satellite_jobs (flag-gated)
app/api/v1/satellite.py
app/schemas/satellite.py
alembic/versions/fr21_0001_satellite_additions.py
scripts/precompute_flood_maps.py
scripts/load_flood_maps.py
data/seeds/satellite_events.yaml
data/replay/sindh-2022/satellite/{manifest.json,*.png}
data/replay/buner-2025/satellite/{manifest.json,*.png}
tests/fixtures/satellite/manifest_small.json (+ 1×1 PNGs)
tests/unit/satellite/test_loader.py
tests/unit/satellite/test_as_of.py
tests/unit/satellite/test_verifier_rules.py
tests/integration/satellite/test_flood_maps_replay.py
tests/integration/satellite/test_verifier_job.py
tests/contract/test_satellite_api.py
```
One-line edits: router include in `app/api/v1/__init__.py`, `register_satellite_jobs()` in `app/main.py`.

## Guardrails
- **D1**: MVP rows are `precomputed=true, simulated=false`. Live job is roadmap behind `FR21_LIVE_VERIFY` (default off).
- **VIII Honest**: every response carries `precomputed`, `simulated` and a label stating the images are real Sentinel-1 captured on `image_date`, processed in advance. Never present a precomputed map as a live detection.
- **No lookahead**: in replay, maps with `image_date` after the replay date are never returned.
- **II / D4**: confirmation status is advisory. No code path in FR21 touches `FundRelease`, `Payment` or fund balances.
- **III**: loading maps writes `SATELLITE_MAPS_LOADED`; each roadmap status change writes `FLOOD_VERIFIED` in the same transaction.
- **IX**: windows, thresholds and verification rules are YAML/settings.
- **VI**: maps are non-PII; donors may read aggregates.

## Definition of Done

MVP:
- [ ] `manifest.json` + PNGs committed for `sindh-2022` (scenario districts' tehsils, ≥ 2 after-dates) and `buner-2025`
- [ ] Loader idempotent; survives FR5 reset (reload on start)
- [ ] `GET /satellite/flood-maps` returns only maps with `image_date ≤ as_of`; DC scoped to own district; images served with auth
- [ ] District summary used by FR8 detail view
- [ ] Contract tests pass; labels present in every item
- [ ] `FR21_LIVE_VERIFY` off → no verification job scheduled; `POST /satellite/verify` returns 404 `FEATURE_DISABLED`

Roadmap:
- [ ] Verification job sets Confirmed / Not confirmed / Pending per UC02 rules with audit and review flag
- [ ] Integration test with a fake gateway covering main, 1a and 4a flows

## Hand-off notes
- **To FR5**: call `load_flood_maps_for_scenario(db, key)` after creating/resetting a scenario run. Do not delete `satellite_flood_map` rows on reset, or call the loader again after deleting.
- **To FR8**: call `SatelliteService.district_summary(db, district_id, as_of=clock.today(), scenario_id)`; show nothing if `None`. Image URLs are API paths requiring the same bearer token.
- **To FR7**: create `TriggerEvent.confirmation_status=PENDING`. FR21 is the only writer after that.
- **To frontend**: use `bbox` with a Leaflet `imageOverlay`; PNGs are EPSG:3857.
