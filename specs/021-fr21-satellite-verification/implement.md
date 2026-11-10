# Implementation Guide: FR21 Satellite Verification

This guide has two parts. **Part A (MVP, D1)** is built for the hackathon: precomputed maps, loader, read API and a dormant flag. **Part B (Roadmap)** is the live verification job behind `FR21_LIVE_VERIFY`.

## Execution order

| Step | Tasks | Part | Outcome |
|---|---|---|---|
| 1 | T001–T005 | A | Settings, YAML windows, fixture, schemas |
| 2 | T007, T013–T015, T019, T021 | A | All MVP tests red |
| 3 | T010, T016–T018, T020, T022 | A | Loader + API green on the fixture manifest |
| 4 | T008–T009, T011 | A | Real precompute (needs FR2 S1 primitives + EE creds); commit files |
| 5 | T012, T006 | A | FR5 hook documented; migration after data-model merge |
| 6 | T023–T028 | B | Verifier and job |
| 7 | T029–T030 | B | Review endpoints |
| 8 | T031–T033 | A/B | Perf, size, lint |

---

## Part A: MVP (D1)

### A1. Event windows (`data/seeds/satellite_events.yaml`)

```yaml
# After-windows must end on/before FR5's scenario end date (data/replay/scenarios.yaml). Verify acquisitions with scripts/ee_smoke_test.py.
sindh-2022:
  orbit_pass: DESCENDING          # set to null if either window lacks descending coverage
  before: ["2022-06-01", "2022-07-10"]
  after:
    - ["2022-08-15", "2022-08-24"]
    - ["2022-08-25", "2022-09-08"]
buner-2025:
  orbit_pass: null
  before: ["2025-07-01", "2025-08-10"]
  after:
    - ["2025-08-15", "2025-08-25"]
control-2021: {}                  # no trigger expected → no maps
```

### A2. Precompute (`app/services/satellite/precompute.py`, script only)

```python
ALGO = "s1-vv-change-v1"

def precompute_scenario(key: str, *, tehsils: list[str] | None = None, dry_run: bool = False) -> Manifest:
    init_ee(settings)
    win = EventWindows.load(settings.SATELLITE_EVENTS_FILE)[key]
    params = dataclasses.replace(S1Params.from_settings(settings), orbit_pass=win.orbit_pass)
    out_dir = Path(settings.SATELLITE_DATA_ROOT) / key / "satellite"
    with SessionLocal() as db:
        scen = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
        districts = district_rows(db, scen.districts)                       # pcode, geom, bbox, area_km2
        t_rows = tehsil_rows(db, [d.id for d in districts], only=tehsils)   # pcode, district_pcode, bbox, area_km2
    maps: list[ManifestMap] = []
    for d in districts:
        region = ee.Geometry(d.geojson_simplified)
        fc = tehsil_feature_collection([t for t in t_rows if t.district_pcode == d.pcode])
        for after in win.after:
            ch = s1_flood_change(region, win.before, after, params)
            if ch.status == "NO_IMAGERY":
                maps += [ManifestMap.no_imagery(t, after, ch) for t in t_rows if t.district_pcode == d.pcode]
                maps.append(ManifestMap.no_imagery_district(d, after, ch))
                continue
            image_date = max(ch.after_dates)
            areas = {r.pcode: r.flooded_km2 for r in flood_area_by_tehsil(ch, fc, params)}
            for t in (t for t in t_rows if t.district_pcode == d.pcode):
                maps.append(_row(level="TEHSIL", district=d, tehsil=t, ch=ch, km2=areas.get(t.pcode, 0.0),
                                 area_km2=t.area_km2, image_date=image_date, bbox=t.bbox,
                                 files=None if dry_run else _write_pngs(out_dir, t.pcode, image_date, ch.image, t.bbox)))
            maps.append(_row(level="DISTRICT", district=d, tehsil=None, ch=ch, km2=sum(areas.values()),
                             area_km2=d.area_km2, image_date=image_date, bbox=d.bbox,
                             files=None if dry_run else _write_pngs(out_dir, d.pcode, image_date, ch.image, d.bbox)))
    manifest = Manifest(scenario_key=key, generated_at=datetime.now(UTC), algorithm=ALGO,
                        params=asdict(params), sources={"s1": settings.S1_COLLECTION,
                        "perm_water": f"{settings.JRC_COLLECTION} occurrence>{params.perm_occurrence}",
                        "dem": settings.SRTM_COLLECTION}, maps=maps)
    if not dry_run:
        (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    return manifest

def _write_pngs(out_dir: Path, code: str, day: date, img: ee.Image, bbox) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    flood_vis = img.select("flooded").selfMask().visualize(palette=["ff2d2d"])
    files = {
        "before": thumbnail_png(img, bbox, VIS_SAR_BEFORE),
        "after": thumbnail_png(img, bbox, VIS_SAR_AFTER),
        "flood": thumbnail_png(flood_vis, bbox, {}),
    }
    names = {}
    for kind, data in files.items():
        name = f"{code}_{day.isoformat()}_{kind}.png"
        (out_dir / name).write_bytes(data)
        names[kind] = name
    return names
```
`flooded_pct = round(100 * km2 / area_km2, 2)`; areas from PostGIS `ST_Area(geom::geography) / 1e6`.

Manifest entry (one per row):
```json
{"level":"TEHSIL","district_pcode":"PK605","tehsil_pcode":"PK60503","status":"OK",
 "before_image_date":"2022-07-08","image_date":"2022-08-22",
 "before_dates":["2022-06-14","2022-06-26","2022-07-08"],"after_dates":["2022-08-22"],
 "flooded_area_km2":212.4,"flooded_pct":11.8,"bbox":[67.10,26.40,67.90,27.10],
 "files":{"before":"PK60503_2022-08-22_before.png","after":"PK60503_2022-08-22_after.png","flood":"PK60503_2022-08-22_flood.png"}}
```

### A3. Loader (`app/services/satellite/loader.py`)

```python
def load_flood_maps_for_scenario(db, key: str, *, actor: str = "system") -> LoadResult:
    scen = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
    if scen is None:
        raise NotFound("SCENARIO_NOT_FOUND")
    root = Path(settings.SATELLITE_DATA_ROOT) / key / "satellite"
    path = root / "manifest.json"
    if not path.exists():
        raise NotFound("MANIFEST_NOT_FOUND")
    m = Manifest.model_validate_json(path.read_text())
    ids = pcode_index(db)                                   # pcode → (district_id, tehsil_id)
    res = LoadResult(scenario_key=key)
    for e in m.maps:
        district_id, tehsil_id = ids.district(e.district_pcode), ids.tehsil(e.tehsil_pcode)
        missing = [f for f in (e.files or {}).values() if not (root / f).exists()]
        res.files_missing += missing
        values = dict(
            district_id=district_id, tehsil_id=tehsil_id, image_date=e.image_date, sensor="SENTINEL1_GRD",
            flooded_area_km2=e.flooded_area_km2, flooded_pct=e.flooded_pct,
            before_tile_url=_rel(root, e.files, "before"), after_tile_url=_rel(root, e.files, "after"),
            flood_mask_url=_rel(root, e.files, "flood"), before_image_date=e.before_image_date,
            bbox=e.bbox, status=e.status, params={"algorithm": m.algorithm, **m.params,
                                                  "before_dates": e.before_dates, "after_dates": e.after_dates},
            precomputed=True, simulated=False, scenario_id=scen.id)
        inserted = upsert_flood_map(db, values)             # ON CONFLICT (uq key) DO UPDATE … RETURNING (xmax = 0)
        res.rows_inserted += int(inserted)
        res.rows_updated += int(not inserted)
        res.no_imagery += int(e.status == "NO_IMAGERY")
    audit.record(db, event_type="SATELLITE_MAPS_LOADED", entity_type="ReplayScenario", entity_id=scen.id,
                 actor=actor, payload={"scenario_key": key, "algorithm": m.algorithm,
                                       "rows": len(m.maps), "inserted": res.rows_inserted,
                                       "no_imagery": res.no_imagery, "generated_at": m.generated_at.isoformat()})
    return res
```
For `NO_IMAGERY` rows, `image_date` is the after-window end and areas are `NULL` (never 0).

### A4. Read side (`app/services/satellite/flood_maps.py`)

```python
def resolve_as_of(db, requested: date | None, scenario_id: UUID | None, clock: Clock) -> date:
    if scenario_id is None:
        return requested or clock.today()
    scen = db.get(ReplayScenario, scenario_id)
    ceiling = scen.sim_date or scen.end_date
    return min(requested, ceiling) if requested else ceiling

def list_maps(db, *, district_id=None, tehsil_id=None, level="TEHSIL", as_of: date,
              scenario_id: UUID | None, latest_only=True) -> list[SatelliteFloodMap]:
    q = select(SatelliteFloodMap).where(SatelliteFloodMap.image_date <= as_of)
    q = q.where(SatelliteFloodMap.scenario_id.is_(None) if scenario_id is None
                else SatelliteFloodMap.scenario_id == scenario_id)
    if tehsil_id:
        q = q.where(SatelliteFloodMap.tehsil_id == tehsil_id)
    elif district_id:
        q = q.where(SatelliteFloodMap.district_id == district_id)
    if level == "TEHSIL":
        q = q.where(SatelliteFloodMap.tehsil_id.is_not(None))
    elif level == "DISTRICT":
        q = q.where(SatelliteFloodMap.tehsil_id.is_(None))
    if latest_only:   # DISTINCT ON (coalesce(tehsil_id, district_id)) … ORDER BY image_date DESC
        q = q.distinct(func.coalesce(SatelliteFloodMap.tehsil_id, SatelliteFloodMap.district_id)).order_by(
            func.coalesce(SatelliteFloodMap.tehsil_id, SatelliteFloodMap.district_id),
            SatelliteFloodMap.image_date.desc())
    else:
        q = q.order_by(SatelliteFloodMap.image_date.desc())
    return list(db.scalars(q))

def district_summary(db, district_id, as_of, scenario_id) -> DistrictFloodSummary | None:
    d_row = next(iter(list_maps(db, district_id=district_id, level="DISTRICT", as_of=as_of,
                                scenario_id=scenario_id)), None)
    if d_row is None or d_row.status == "NO_IMAGERY":
        return None
    tehsils = [m for m in list_maps(db, district_id=district_id, level="TEHSIL", as_of=as_of,
                                    scenario_id=scenario_id) if m.image_date == d_row.image_date]
    top = sorted(tehsils, key=lambda m: m.flooded_pct or 0, reverse=True)[:5]
    return DistrictFloodSummary.from_rows(d_row, top, as_of)

def image_path(m: SatelliteFloodMap, kind: Literal["before", "after", "flood"]) -> Path:
    rel = {"before": m.before_tile_url, "after": m.after_tile_url, "flood": m.flood_mask_url}[kind]
    if not rel:
        raise NotFound("IMAGE_NOT_FOUND")
    p = (Path.cwd() / rel).resolve()
    allowed = [Path(settings.SATELLITE_DATA_ROOT).resolve(), Path(settings.SATELLITE_LIVE_ROOT).resolve()]
    if not any(p.is_relative_to(a) for a in allowed) or not p.exists():
        raise NotFound("IMAGE_NOT_FOUND")
    return p
```
DC scoping: in the router, resolve the district (from the tehsil if given) and call the foundation helper that raises `403 FORBIDDEN_DISTRICT` when `user.role == DC and user.district_id != district_id`.

Labels:
```python
LABEL_EN = "Precomputed from real Sentinel-1 radar images captured on {date}. Permanent water removed."
LABEL_UR = "یہ نقشہ {date} کی اصل سینٹینل-1 ریڈار تصاویر سے پہلے سے تیار کیا گیا ہے۔ مستقل پانی شامل نہیں۔"
LIVE_EN  = "Detected from Sentinel-1 radar captured on {date}. Permanent water removed."   # roadmap rows
```

### A5. Flag stub (`app/services/satellite/jobs.py`)

```python
def register_satellite_jobs(scheduler) -> None:
    if not is_enabled("FR21_LIVE_VERIFY"):
        log.info("fr21_live_verify_disabled")
        return
    scheduler.register_job("satellite.verify_floods", run_verification_job, cron=settings.FR21_VERIFY_CRON)
```
Routes `GET /satellite/verifications` and `POST /satellite/verify` start with `if not is_enabled("FR21_LIVE_VERIFY"): raise NotFound("FEATURE_DISABLED")`.

### A6. FR5 hook (to hand to the FR5 owner)

```python
# in FR5's start/reset service, after the ReplayScenario row exists and before stepping:
from app.services.satellite.loader import load_flood_maps_for_scenario
try:
    load_flood_maps_for_scenario(db, scenario.key, actor="system")
except NotFound:            # e.g. control-2021 has no manifest
    pass
```

---

## Part B: Roadmap (behind `FR21_LIVE_VERIFY`)

### B1. Eligibility and window (`app/services/satellite/verifier.py`)

```python
def window_for(db, event: TriggerEvent) -> tuple[date, date]:
    fired = db.scalars(select(FundRelease.created_at).where(FundRelease.event_id == event.id)).all()
    start = event.opened_at.date()
    end = max(fired).date() + timedelta(days=settings.FR21_VERIFY_WINDOW_DAYS)
    return start, end

def eligible_events(db, today: date, scenario_id) -> list[TriggerEvent]:
    q = (select(TriggerEvent)
         .where(TriggerEvent.confirmation_status == "PENDING",
                TriggerEvent.confirmation_review_required.is_(False))
         .where(scoped_scenario(TriggerEvent, scenario_id)))
    return [e for e in db.scalars(q) if today <= window_for(db, e)[1] + timedelta(days=1)]  # one run after close
```

### B2. Decision rule (pure, unit-tested)

```python
@dataclass(frozen=True)
class Decision:
    status: Literal["PENDING", "CONFIRMED", "NOT_CONFIRMED"]
    review_required: bool
    reason: str

def decide(*, total_km2: float | None, max_tehsil_pct: float | None, images_seen: int,
           today: date, window_end: date, min_km2: float, min_pct: float) -> Decision:
    if images_seen and ((total_km2 or 0) >= min_km2 or (max_tehsil_pct or 0) >= min_pct):
        return Decision("CONFIRMED", False, "threshold_passed")
    if today <= window_end:
        return Decision("PENDING", False, "no_image_yet" if not images_seen else "below_threshold_window_open")
    if images_seen:
        return Decision("NOT_CONFIRMED", True, "below_threshold_window_closed")      # UC02-4a
    return Decision("PENDING", True, "no_imagery_window_closed")                     # UC02-1a, review
```

### B3. Verify one event

```python
def verify_event(self, db, event: TriggerEvent, now: datetime) -> Decision:
    today = now.date()
    w_start, w_end = window_for(db, event)
    district = db.get(District, event.district_id)
    region = district_region(db, district.id)
    since = (event.confirmation_checked_at or datetime.combine(w_start, time.min, UTC)).date()
    try:
        new_dates = [d for d in self.source.acquisitions(region, since, min(today, w_end)) if d >= w_start]
        best_total, best_pct, seen = None, None, len(prior_maps(db, event.id)) > 0
        base = (w_start - timedelta(days=settings.FR21_BASELINE_GAP_DAYS + settings.FR21_BASELINE_DAYS),
                w_start - timedelta(days=settings.FR21_BASELINE_GAP_DAYS))
        for d in new_dates:
            ch = self.source.flood_change(region, base, (d, d))
            if ch.status != "OK":
                continue
            seen = True
            rows = self.source.areas_by_tehsil(ch, district.id)
            persist_live_maps(db, event, d, ch, rows)                  # precomputed=False, event_id
            total, pct = sum(r.km2 for r in rows), max(r.pct for r in rows)
            best_total = max(best_total or 0, total); best_pct = max(best_pct or 0, pct)
        dec = decide(total_km2=best_total, max_tehsil_pct=best_pct, images_seen=int(seen),
                     today=today, window_end=w_end, min_km2=settings.FR21_CONFIRM_MIN_KM2,
                     min_pct=settings.FR21_CONFIRM_MIN_PCT)
    except EEUnavailable as exc:
        update_source_status(db, source=DataSource.SENTINEL1, healthy=False, last_valid_date=None,
                             error=str(exc), now=now, scenario_id=event.scenario_id)
        dec = Decision("PENDING", False, "source_unavailable")
    old = event.confirmation_status
    event.confirmation_checked_at = now
    event.confirmation_review_required = dec.review_required
    event.confirmation_detail = {"reason": dec.reason, "window": [str(w_start), str(w_end)],
                                 "total_km2": best_total, "max_tehsil_pct": best_pct,
                                 "rule": f"district_km2>={settings.FR21_CONFIRM_MIN_KM2} OR tehsil_pct>={settings.FR21_CONFIRM_MIN_PCT}"}
    if dec.status != old:
        event.confirmation_status = dec.status
        audit.record(db, event_type="FLOOD_VERIFIED", entity_type="TriggerEvent", entity_id=event.id,
                     actor="system", payload={"from": old, "to": dec.status, **event.confirmation_detail})
    if dec.review_required:
        alerts.enqueue(db, recipient=pdma_officers_for(district), purpose="VERIFICATION_REVIEW",
                       template_key="verification_review", context={"district": district.name, "reason": dec.reason},
                       related={})
    return dec
```
`run_verification_job` loops `eligible_events`, one transaction per event, holding `pg_try_advisory_xact_lock(hashtext('satellite:verify'))`. `self.source` is `LiveS1Source` (FR2 gateway) or `ReplayVerificationSource` (precomputed rows ≤ replay date; no EE).

## Configuration

| Key | Default | Part |
|---|---|---|
| `SATELLITE_EVENTS_FILE` | `data/seeds/satellite_events.yaml` | A |
| `SATELLITE_DATA_ROOT` | `data/replay` | A |
| `SATELLITE_LIVE_ROOT` | `data/satellite` | B |
| `FLAGS` contains `FR21_LIVE_VERIFY` | off | B |
| `FR21_VERIFY_WINDOW_DAYS` | 7 | B |
| `FR21_CONFIRM_MIN_KM2` / `FR21_CONFIRM_MIN_PCT` | 5.0 / 1.0 | B |
| `FR21_BASELINE_DAYS` / `FR21_BASELINE_GAP_DAYS` | 60 / 7 | B |
| `FR21_VERIFY_CRON` | `0 3 * * *` (UTC) | B |
| S1 thresholds | FR2 settings (`S1_*`, `JRC_*`) | A/B |

## Commands

```bash
# Part A
python scripts/precompute_flood_maps.py --scenario sindh-2022 --dry-run     # prints km² table, no files
python scripts/precompute_flood_maps.py --scenario sindh-2022
python scripts/precompute_flood_maps.py --scenario buner-2025
python scripts/load_flood_maps.py --scenario sindh-2022
pytest -q tests/unit/satellite tests/integration/satellite/test_flood_maps_replay.py tests/contract/test_satellite_api.py
curl -H "Authorization: Bearer $OFFICER" -H "X-Scenario-Id: $SINDH" \
     "localhost:8000/api/v1/satellite/flood-maps?district=PK605&level=ALL"
# Part B
FLAGS=FR21_LIVE_VERIFY pytest -q tests/unit/satellite/test_verifier_rules.py tests/integration/satellite/test_verifier_job.py
```
(`PK605` is illustrative; use the Dadu P-code from the seeded boundaries.)

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1 | Manual: `manifest.json` lists every scenario tehsil × after-window; images open |
| US1-2 | `test_loader.py::test_no_imagery_not_zero` |
| US1-3 | `test_loader.py::test_idempotent_and_labels` |
| US1-4 | Manual in rehearsal: FR5 reset → start → maps visible |
| US2-1, US2-2 | `test_flood_maps_replay.py::test_no_lookahead`, `test_as_of.py` |
| US2-3 | `test_satellite_api.py::test_dc_scoping` |
| US2-4 | `test_satellite_api.py::test_labels_present` |
| US2-5 | `test_satellite_api.py::test_image_auth_and_traversal` |
| US2-6 | `test_flood_maps_replay.py::test_live_mode_excludes_replay` |
| US3-1, US3-2 | `test_flood_maps_replay.py -k summary` |
| US4-1..6 | `test_verifier_rules.py`, `test_verifier_job.py` |
| US5-1..2 | `test_satellite_api.py -k verifications` |
| SC-002 | `test_no_lookahead` + rehearsal check |
| SC-003 | T031 |
| SC-006 | `test_verifier_job.py::test_no_money_tables_touched` |

## Demo notes

- **Sindh 2022 (hero)**: as the replay slider crosses the first after-date, the Dadu map gains a "Satellite" toggle: before (dry June/July radar) vs after (dark flood water), red overlay, "212 km² newly flooded in Johi". Say: "This is real Sentinel-1 radar, precomputed for the replay; in production the system checks each new pass automatically."
- **DC approval**: the Action-tier detail shows the district summary if an after-date ≤ replay date exists. If the Action tier fires before the first after-date (likely, since the system anticipates), the panel is empty, which illustrates "anticipate, then verify".
- **Buner 2025**: expect modest detected water (steep valleys, fast recession). Present it honestly as a radar limitation for flash floods; the label covers it.
- **Control 2021**: no trigger, so no maps.

## Pitfalls

- Never return maps after the replay date, even if the client asks: the clamp lives in the service, not the router.
- The loader must not run against `scenario_id=NULL`, or replay maps leak into live views.
- `DISTINCT ON` requires the `ORDER BY` to start with the same expression; keep them identical.
- `Path.is_relative_to` after `resolve()` is the traversal guard; do not build paths from `kind` strings directly.
- PNG files are part of the demo: run the loader in the deployed container and confirm `files_missing` is empty.
- Thumbnails are EPSG:3857; giving Leaflet a 4326 bbox is still correct (Leaflet projects the corners), but the PNG must have been rendered in 3857 for straight edges to align.
- Roadmap: do not mark Not confirmed on the first dry image; triggers precede floods.
- Roadmap: the job must never import payment or allocation services; the test snapshots money tables.
