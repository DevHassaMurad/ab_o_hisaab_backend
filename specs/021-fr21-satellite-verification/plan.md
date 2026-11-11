# Implementation Plan: FR21 Satellite Verification

**Branch**: `021-fr21-satellite-verification` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/021-fr21-satellite-verification/spec.md`

## Summary

**MVP (D1)**: `scripts/precompute_flood_maps.py` reads event windows from `data/seeds/satellite_events.yaml`, calls FR2's Sentinel-1 primitives for each scenario tehsil and district, downloads before/after/flood PNGs and writes `data/replay/<key>/satellite/manifest.json`. `load_flood_maps_for_scenario()` turns the manifest into `SatelliteFloodMap` rows with the scenario's `scenario_id`, `precomputed=true`, `simulated=false`. `app/api/v1/satellite.py` lists maps as of a date (clamped to the replay clock), returns a district summary for FR8 and streams images with role checks.

**Roadmap (`FR21_LIVE_VERIFY`)**: `run_verification_job()` (daily, 03:00 UTC) selects eligible `TriggerEvent`s, asks FR2 for new Sentinel-1 acquisitions, computes new water per tehsil, stores non-precomputed map rows linked to the event and sets `confirmation_status` with audit, review flag and PDMA notice.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: fastapi, pydantic v2, sqlalchemy 2.x + geoalchemy2, pyyaml, FR2's `app/integrations/earth_engine.py` (script and roadmap job only). The API and loader never import `ee`.

**Storage**: PostgreSQL + PostGIS (`satellite_flood_map`, `trigger_event`, `replay_scenario`, `tehsil`, `district`). Files: `data/replay/<key>/satellite/*.png` + `manifest.json` (committed; ≈ 3 PNGs × ~150 KB × ~40 rows ≈ 18 MB per scenario, acceptable; use Git LFS if the repo policy requires).

**Testing**: pytest; manifest fixture with tiny PNGs; fake FR2 gateway for the roadmap job; contract tests for roles and labels.

**Target Platform**: Linux container (API, loader); the precompute script runs on a dev laptop with EE credentials.

**Project Type**: web-service + offline script.

**Performance Goals**: list/summary p95 < 500 ms (indexed on `scenario_id, tehsil_id, image_date`); image response < 1 s (`FileResponse`, `Cache-Control: private, max-age=86400`); loader < 5 s per scenario.

**Constraints**: no lookahead in replay; no PII; no writes to money tables; flag default off.

**Scale/Scope**: 2 scenarios × (≈ 5–8 districts × ≈ 4–7 tehsils) × 1–2 after-dates ≈ 40–100 rows.

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| I Anticipate | Verification runs after money moves and never delays it | ✅ |
| II Human authority | Advisory only; no code path touches releases, approvals or payments (test asserts it) | ✅ |
| III Traceability | `SATELLITE_MAPS_LOADED` per load; `FLOOD_VERIFIED` per status change, same transaction | ✅ |
| IV Explainable | `params` stores thresholds, image dates/ids, algorithm version; audit payload repeats the rule | ✅ |
| V Fair | n/a | ✅ |
| VI Privacy | No PII; DC scoped; donors read aggregates only | ✅ |
| VII Degrade gracefully | No imagery → Pending (not Not confirmed); EE failure → Pending + `SENTINEL1` unhealthy | ✅ |
| VIII Honest prototype | `precomputed` + label on every item; `simulated=false` per D1 (tension with VIII's list noted in clarify #7) | ⚠️ |
| IX Config over code | Windows in YAML; thresholds in settings | ✅ |
| X Simplicity | MVP is files + rows + 3 read endpoints; live job behind a flag | ✅ |
| XI Tests | Not a money path; tests for no-lookahead, scoping, loader idempotency and verifier rules | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Files are the source of truth; DB rows are loaded per scenario | Survives FR5 resets; demo works offline | Rows only (lost on reset); live `scenario_id=NULL` rows (leak into live views) |
| PNG thumbnails, EPSG:3857, stored `bbox` | Simple Leaflet `imageOverlay`; no EE token at demo time | EE map tiles via `getMapId` (expire, need network) |
| Authenticated image endpoint | Keeps DC scoping | Public `StaticFiles` mount |
| District rows via `tehsil_id=NULL` | One table, mirrors `RiskIndex` | Separate district map table (new entity, forbidden by guide) |
| As-of clamp to `ReplayScenario.sim_date` | Honest replay | Trust the client `date` |
| Dry pre-monsoon baseline | Avoids wet-July masking in 2022 | Image immediately before trigger |
| Not confirmed only at window close | Anticipatory triggers precede floods | Decide on first image (false Not confirmed) |
| Roadmap job at 03:00 UTC daily | After the 01:00 pipeline; S1 GRD typically appears in EE within ~1–2 days (verify) | Event-driven on new image (needs Pub/Sub; YAGNI) |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| SatelliteFloodMap | all | MVP: `tehsil_id`, `image_date`, `sensor="SENTINEL1_GRD"`, `flooded_area_km2`, `flooded_pct`, `before_tile_url`, `after_tile_url`, `precomputed=true`, `scenario_id` (+ proposed fields). Roadmap: same with `precomputed=false`, `event_id` |
| ReplayScenario | `id`, `key`, `sim_date`, `end_date`, `districts` | — |
| Tehsil / District | `id`, `pcode`, `name`, `district_id`, `geom` | — |
| TriggerEvent (roadmap) | `id`, `district_id`, `opened_at`, `status`, `scenario_id` | `confirmation_status` (+ proposed review fields) |
| FundRelease (roadmap) | `event_id`, `tier`, `created_at`/`released_at` | — |
| DataSourceStatus (roadmap) | — | `SENTINEL1` row |
| AuditLog / Alert | — | via services |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Why |
|---|---|---|
| SatelliteFloodMap | `simulated: bool, default false` | D1 says rows are stored with `simulated=false`; the column is missing |
| SatelliteFloodMap | `district_id: FK District, required`; `tehsil_id` becomes nullable (NULL = district-level row) | District summary and images for FR8 (UC07-2) |
| SatelliteFloodMap | `before_image_date: date, nullable` | `image_date` is the after date; the before date must be shown |
| SatelliteFloodMap | `flood_mask_url: str, nullable` | Flood overlay image (UC02-5 "before and after flood maps") |
| SatelliteFloodMap | `bbox: JSONB` `[west, south, east, north]` (EPSG:4326) | Exact overlay placement for the PNGs |
| SatelliteFloodMap | `status: enum OK, NO_IMAGERY` | "No imagery" ≠ zero flooding (FR21-003) |
| SatelliteFloodMap | `params: JSONB` (algorithm version, thresholds, before/after acquisition dates, orbit) | Principle IV |
| SatelliteFloodMap | `event_id: FK TriggerEvent, nullable` | Roadmap: link verification maps to the event |
| SatelliteFloodMap | Unique `NULLS NOT DISTINCT (district_id, tehsil_id, image_date, sensor, scenario_id)` | Idempotent loads |
| TriggerEvent | `confirmation_checked_at: timestamptz, nullable`, `confirmation_review_required: bool default false`, `confirmation_detail: JSONB, nullable` | Roadmap UC02-1a retry bookkeeping and UC02-4a review flag |
| Alert | `purpose` += `VERIFICATION_REVIEW` | Roadmap PDMA notice |

## API contracts

Router `app/api/v1/satellite.py`, prefix `/api/v1/satellite`, OpenAPI tag `Reporting`. All endpoints honour `X-Scenario-Id`.

### `GET /api/v1/satellite/flood-maps`, roles `PDMA_OFFICER`, `ADMIN`, `DC` (own district), `DONOR`

Query: `tehsil` (pcode or UUID) **or** `district` (pcode or UUID), optional `date` (YYYY-MM-DD), `level` (`TEHSIL` default | `DISTRICT` | `ALL`), `latest_only` (bool, default `true`: one row per tehsil), `page`, `page_size`.

Response `200`:
```json
{
  "as_of": "2022-08-30",
  "scenario_key": "sindh-2022",
  "items": [
    {
      "id": "5c1d…",
      "level": "TEHSIL",
      "district_pcode": "PK605",
      "tehsil_pcode": "PK60503",
      "tehsil_name": "Johi",
      "sensor": "SENTINEL1_GRD",
      "status": "OK",
      "before_image_date": "2022-07-08",
      "image_date": "2022-08-22",
      "flooded_area_km2": 212.4,
      "flooded_pct": 11.8,
      "bbox": [67.10, 26.40, 67.90, 27.10],
      "before_url": "/api/v1/satellite/flood-maps/5c1d…/image/before",
      "after_url": "/api/v1/satellite/flood-maps/5c1d…/image/after",
      "flood_url": "/api/v1/satellite/flood-maps/5c1d…/image/flood",
      "precomputed": true,
      "simulated": false,
      "label_en": "Precomputed from real Sentinel-1 radar images captured on 22 Aug 2022. Permanent water removed.",
      "label_ur": "[Urdu text]"
    }
  ],
  "total": 1, "page": 1, "page_size": 50
}
```
Values in the example are illustrative. Empty result: `200` with `items: []`.

Errors: `400 MISSING_FILTER` (neither `tehsil` nor `district`), `404 TEHSIL_NOT_FOUND` / `DISTRICT_NOT_FOUND`, `403 FORBIDDEN_DISTRICT` (DC), `422 VALIDATION_ERROR` (bad date).

### `GET /api/v1/satellite/flood-maps/district-summary`, same roles

Query: `district` (required), `date` (optional, same clamp).

Response `200`:
```json
{
  "as_of": "2022-08-30",
  "district_pcode": "PK605",
  "image_date": "2022-08-22",
  "before_image_date": "2022-07-08",
  "total_flooded_km2": 640.2,
  "flooded_pct": 8.4,
  "top_tehsils": [
    {"tehsil_pcode": "PK60503", "name": "Johi", "flooded_area_km2": 212.4, "flooded_pct": 11.8}
  ],
  "images": {"before_url": "…/image/before", "after_url": "…/image/after", "flood_url": "…/image/flood", "bbox": [67.0, 26.2, 68.1, 27.3]},
  "precomputed": true,
  "simulated": false,
  "label_en": "…", "label_ur": "…"
}
```
No maps → `200` with `null` body fields except `as_of` and `district_pcode` (frontend shows nothing). Errors as above.

### `GET /api/v1/satellite/flood-maps/{map_id}/image/{kind}`, same roles

`kind ∈ {before, after, flood}`. Returns `image/png` via `FileResponse`, `Cache-Control: private, max-age=86400`. The resolved path must be inside `data/replay/` (or `data/satellite/` for roadmap live maps). Errors: `404 MAP_NOT_FOUND`, `404 IMAGE_NOT_FOUND`, `403 FORBIDDEN_DISTRICT`, `422 INVALID_KIND`.

### `POST /api/v1/satellite/flood-maps/load`, role `ADMIN`

Request `{"scenario_key": "sindh-2022"}` → `200 {"scenario_key":"sindh-2022","rows_inserted":38,"rows_updated":0,"no_imagery":2,"files_missing":[]}`. Errors: `404 SCENARIO_NOT_FOUND`, `404 MANIFEST_NOT_FOUND`, `422 MANIFEST_INVALID`.

### Roadmap (404 `FEATURE_DISABLED` when `FR21_LIVE_VERIFY` is off)

`GET /api/v1/satellite/verifications`, roles `PDMA_OFFICER`, `ADMIN`, `DC` (own district). Query: `district`, `status` (`PENDING|CONFIRMED|NOT_CONFIRMED`), `review_required` (bool).
```json
{
  "items": [
    {
      "event_id": "e8a2…",
      "district_pcode": "PK605",
      "opened_at": "2022-08-14T01:05:00Z",
      "highest_tier": "ACTION",
      "confirmation_status": "CONFIRMED",
      "confirmation_checked_at": "2022-08-23T03:00:00Z",
      "review_required": false,
      "detail": {"rule": "district_km2>=5 OR tehsil_pct>=1", "total_flooded_km2": 640.2, "max_tehsil_pct": 11.8, "after_dates": ["2022-08-22"], "window_end": "2022-08-29"}
    }
  ],
  "total": 1, "page": 1, "page_size": 50
}
```

`POST /api/v1/satellite/verify`, role `ADMIN`. Request `{"event_id": "e8a2…"}` → `200` with the item above. Errors: `404 EVENT_NOT_FOUND`, `409 VERIFY_ALREADY_RUNNING`, `404 FEATURE_DISABLED`.

## Scheduled jobs

| Job | Schedule | Function | Notes |
|---|---|---|---|
| `satellite.verify_floods` (roadmap) | `0 3 * * *` UTC, registered only when `is_enabled("FR21_LIVE_VERIFY")` | `app/services/satellite/jobs.py: run_verification_job(db, clock)` | Advisory lock `hashtext('satellite:verify')`; also callable by FR5 per replay day in the roadmap replay mode |

## Project Structure

### Documentation (this feature)
```text
specs/021-fr21-satellite-verification/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/
├── services/satellite/
│   ├── __init__.py
│   ├── flood_maps.py       # SatelliteService.list_maps, district_summary, resolve_as_of, image_path
│   ├── loader.py           # Manifest pydantic model, load_flood_maps_for_scenario
│   ├── precompute.py       # EventWindows (YAML), precompute_scenario → Manifest + PNGs (uses FR2)
│   ├── verifier.py         # roadmap: FloodVerifier, decide(), eligible_events()
│   └── jobs.py             # register_satellite_jobs(scheduler) – flag-gated
├── api/v1/satellite.py
└── schemas/satellite.py    # FloodMapOut, FloodMapPage, DistrictFloodSummary, LoadRequest/Result, VerificationOut
alembic/versions/fr21_0001_satellite_additions.py   # after proposals are merged
scripts/precompute_flood_maps.py    # --scenario sindh-2022 [--tehsil PK60503] [--dry-run]
scripts/load_flood_maps.py          # --scenario sindh-2022
data/seeds/satellite_events.yaml
data/replay/sindh-2022/satellite/{manifest.json, <pcode>_<date>_{before,after,flood}.png}
data/replay/buner-2025/satellite/{…}
tests/fixtures/satellite/{manifest_small.json, px.png}
tests/unit/satellite/{test_loader,test_as_of,test_verifier_rules}.py
tests/integration/satellite/{test_flood_maps_replay,test_verifier_job}.py
tests/contract/test_satellite_api.py
```

**Structure Decision**: single project; FR21 owns `app/services/satellite/` and the `satellite` router named in the constitution layout.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| ⚠️ VIII tension: precomputed maps stored `simulated=false` | D1 explicitly specifies these values; the imagery is real | Setting `simulated=true` would mislabel real satellite data as fake; the `precomputed` flag + label meets the honesty goal. Flagged for the constitution owner (clarify #7) |
