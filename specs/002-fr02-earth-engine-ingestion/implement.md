# Implementation Guide: FR2 Earth Engine Ingestion

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T003 | Dependency, settings, smoke test run (resolve IMERG latency) |
| 2 | T004–T007 | EE seam, boundaries |
| 3 | T008–T009 | US1 tests red |
| 4 | T010–T014 | IMERG ingest green, live parity check |
| 5 | T015–T018 | Replay source and committed caches |
| 6 | T023–T027 | S1 primitives (unblocks FR21) |
| 7 | T019–T022 | Terrain seed |
| 8 | T028–T029 | Stale path |
| 9 | T030–T032 | Timing, tuning, lint |

## Key algorithms

### 1. Init and retry (`app/integrations/earth_engine.py`)

```python
import ee

_INIT = False

def init_ee(s: Settings) -> None:
    global _INIT
    if _INIT:
        return
    if not (s.GEE_SERVICE_ACCOUNT and s.GEE_KEY_FILE and Path(s.GEE_KEY_FILE).exists()):
        raise EEUnavailable("EE not configured")
    creds = ee.ServiceAccountCredentials(s.GEE_SERVICE_ACCOUNT, s.GEE_KEY_FILE)
    url = "https://earthengine-highvolume.googleapis.com" if s.GEE_HIGH_VOLUME else None
    ee.Initialize(credentials=creds, project=s.GEE_PROJECT, opt_url=url)
    _INIT = True

_RETRYABLE = ("Too many concurrent", "Computation timed out", "Internal error", "429", "503")

def _call(fn, *a, **kw):
    for attempt in range(4):
        try:
            return fn(*a, **kw)
        except ee.EEException as exc:
            if attempt == 3 or not any(t in str(exc) for t in _RETRYABLE):
                raise EEUnavailable(str(exc)) from exc
            time.sleep(5 * 2 ** attempt)
```

### 2. Tehsil collection from PostGIS (`ee_boundaries.py` + `earth_engine.py`)

```python
SQL = text("""
  SELECT t.id, t.pcode,
         ST_AsGeoJSON(ST_SimplifyPreserveTopology(t.geom, :tol))::json AS g,
         ST_Y(ST_PointOnSurface(t.geom)) AS lat, ST_X(ST_PointOnSurface(t.geom)) AS lon
  FROM tehsil t JOIN district d ON d.id = t.district_id
  WHERE d.is_active AND (:ids IS NULL OR t.id = ANY(:ids))
""")

def tehsil_feature_collection(rows) -> ee.FeatureCollection:
    feats = [ee.Feature(ee.Geometry(r.g), {"pcode": r.pcode}) for r in rows]
    return ee.FeatureCollection(feats)
```
`ST_PointOnSurface` (not centroid) guarantees the fallback point is inside oddly shaped tehsils.

### 3. IMERG daily image: total + max 3 h

```python
def imerg_daily_image(day: date, s: Settings) -> ee.Image:
    start = ee.Date(day.isoformat())                       # UTC midnight
    col = (ee.ImageCollection(s.IMERG_COLLECTION)
           .filterDate(start, start.advance(1, "day"))
           .select(s.IMERG_BAND)
           .sort("system:time_start"))
    n = col.size()
    lst = col.toList(48)
    daily = col.sum().multiply(0.5).rename("daily_mm")     # mm/hr × 0.5 h per slot

    def window_sum(i):
        i = ee.Number(i)
        return ee.ImageCollection.fromImages(lst.slice(i, i.add(6))).sum().multiply(0.5)

    windows = ee.List.sequence(0, n.subtract(6)).map(window_sum)
    max3h = ee.ImageCollection.fromImages(windows).max().rename("max3h_mm")
    return daily.addBands(max3h).set("n_images", n)

def imerg_image_count(day: date, s: Settings) -> int:
    start = ee.Date(day.isoformat())
    return int(_call(ee.ImageCollection(s.IMERG_COLLECTION)
                     .filterDate(start, start.advance(1, "day")).size().getInfo))

def reduce_imerg(day: date, fc: ee.FeatureCollection, points: dict[str, tuple[float, float]], s: Settings) -> list[TehsilRain]:
    img = imerg_daily_image(day, s)
    out = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=s.IMERG_REDUCE_SCALE_M, tileScale=2)
    feats = _call(out.getInfo)["features"]
    rows, missing = [], []
    for f in feats:
        p = f["properties"]
        if p.get("daily_mm") is None:
            missing.append(p["pcode"])
            continue
        rows.append(TehsilRain(p["pcode"], round(p["daily_mm"], 2), round(p["max3h_mm"], 2), flags=[]))
    if missing:                                            # tiny tehsils: sample the point on surface
        pts = ee.FeatureCollection([ee.Feature(ee.Geometry.Point(points[c][1], points[c][0]), {"pcode": c}) for c in missing])
        samp = _call(img.reduceRegions(collection=pts, reducer=ee.Reducer.first(), scale=11132).getInfo)["features"]
        for f in samp:
            p = f["properties"]
            rows.append(TehsilRain(p["pcode"], round(p["daily_mm"], 2), round(p["max3h_mm"], 2),
                                   flags=["centroid_fallback"]))
    return rows
```
With a multi-band image, `Reducer.first()` and `Reducer.mean()` name output properties after the bands (`daily_mm`, `max3h_mm`) (verify). Batch `fc` in chunks of `EE_TEHSIL_BATCH` when it is larger.

Pure-Python reference used in tests (`imerg_math.py`):
```python
def daily_total(rates_mm_hr: Sequence[float]) -> float:
    return round(sum(rates_mm_hr) * 0.5, 2)

def rolling_max_3h(rates_mm_hr: Sequence[float]) -> float | None:
    if len(rates_mm_hr) < 6:
        return None
    acc = [r * 0.5 for r in rates_mm_hr]
    return round(max(sum(acc[i:i + 6]) for i in range(len(acc) - 5)), 2)
```
Parity test (live): for one pixel-sized tehsil, per-pixel max equals the series max; for larger tehsils EE value ≥ max of the mean series (document, don't assert equality).

### 4. Ingestor (`app/services/ingestion/imerg_ingestor.py`)

```python
class ImergIngestor:
    source = DataSource.GPM_IMERG

    def ingest(self, db, *, start=None, end=None, district_ids=None) -> IngestResult:
        today = self.clock.today()
        end = end or today - timedelta(days=1)
        start = start or end - timedelta(days=settings.IMERG_LOOKBACK_DAYS - 1)
        scenario_id = current_scenario()
        tehsils = active_tehsils(db, district_ids, scenario_id)
        src = self.source_for(scenario_id, today)          # LiveImergSource(gateway) | CachedImergSource
        per_day, errors = {}, []
        for day in daterange(start, end):                  # network first, sequential (EE quota)
            try:
                n = src.image_count(day)
                due = day <= today - timedelta(days=settings.IMERG_EXPECTED_LAG_DAYS)
                if n == 48 or (due and n >= settings.IMERG_MIN_IMAGES_PARTIAL):
                    rows = src.reduce(day, tehsils)
                    if n < 48:
                        for r in rows: r.flags.append(f"imerg_partial:{n}/48")
                    per_day[day] = rows
                # else: not yet published (skip) or overdue-incomplete (stale path below)
            except EEUnavailable as exc:
                errors.append({"scope": "earth_engine", "date": str(day), "error": str(exc)})
                break                                       # EE is down; don't hammer it
        # one transaction: lock, upsert, per-tehsil copy-forward, status, audit (same as FR1)
        ...
```
`LiveImergSource.reduce()` calls `reduce_imerg`; `CachedImergSource.image_count()` returns `n_images` from the CSV (or 0 when the date is past the no-lookahead cut-off `replay_today − REPLAY_IMERG_LAG_DAYS`).

### 5. Sentinel-1 change detection (used by FR21)

```python
@dataclass(frozen=True)
class S1Params:
    diff_db: float = -3.0
    after_max_db: float = -15.0
    speckle_radius_m: int = 50
    perm_occurrence: int = 80
    max_slope_deg: float = 5.0
    min_connected_px: int = 8
    area_scale_m: int = 30
    orbit_pass: str | None = None          # "ASCENDING" | "DESCENDING" | None

    @classmethod
    def from_settings(cls, s: Settings) -> "S1Params": ...

def s1_collection(region, start: date, end: date, orbit_pass=None) -> ee.ImageCollection:
    c = (ee.ImageCollection(settings.S1_COLLECTION)
         .filterBounds(region)
         .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
         .filter(ee.Filter.eq("instrumentMode", "IW"))
         .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
         .select("VV"))
    return c.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass)) if orbit_pass else c

def s1_acquisitions(region, start, end, orbit_pass=None) -> list[date]:
    ms = _call(s1_collection(region, start, end, orbit_pass).aggregate_array("system:time_start").getInfo)
    return sorted({datetime.fromtimestamp(m / 1000, tz=UTC).date() for m in ms})

def s1_flood_change(region, before: tuple[date, date], after: tuple[date, date], p: S1Params) -> S1Change:
    b_dates = s1_acquisitions(region, *before, p.orbit_pass)
    a_dates = s1_acquisitions(region, *after, p.orbit_pass)
    if not b_dates or not a_dates:
        return S1Change(image=None, before_dates=b_dates, after_dates=a_dates, status="NO_IMAGERY")
    smooth = lambda i: i.focal_median(p.speckle_radius_m, "circle", "meters")
    b = smooth(s1_collection(region, *before, p.orbit_pass).median())
    a = smooth(s1_collection(region, *after, p.orbit_pass).sort("system:time_start").mosaic())  # latest on top
    new_water = a.subtract(b).lt(p.diff_db).And(a.lt(p.after_max_db))
    perm = ee.Image(settings.JRC_COLLECTION).select("occurrence").gt(p.perm_occurrence).unmask(0)
    slope = ee.Terrain.slope(ee.Image(settings.SRTM_COLLECTION).select("elevation"))
    flooded = (new_water.And(perm.Not()).And(slope.lt(p.max_slope_deg)))
    flooded = flooded.updateMask(flooded.connectedPixelCount(100, True).gte(p.min_connected_px))
    img = (b.rename("before_vv").addBands(a.rename("after_vv"))
           .addBands(flooded.rename("flooded").unmask(0)).clip(region))
    return S1Change(image=img, before_dates=b_dates, after_dates=a_dates, status="OK")

def flood_area_by_tehsil(ch: S1Change, fc: ee.FeatureCollection, p: S1Params) -> list[TehsilFlood]:
    area = ch.image.select("flooded").multiply(ee.Image.pixelArea()).rename("flooded_m2")
    out = area.reduceRegions(collection=fc, reducer=ee.Reducer.sum(), scale=p.area_scale_m, tileScale=8)
    feats = _call(out.getInfo)["features"]
    return [TehsilFlood(f["properties"]["pcode"], round(f["properties"]["sum"] / 1e6, 3)) for f in feats]

def thumbnail_png(image: ee.Image, bbox: tuple[float, float, float, float], vis: dict, dimensions: int = 1024) -> bytes:
    region = ee.Geometry.Rectangle(list(bbox), proj="EPSG:4326", geodesic=False)
    url = _call(image.getThumbURL, {**vis, "region": region, "dimensions": dimensions,
                                    "format": "png", "crs": "EPSG:3857"})
    r = httpx.get(url, timeout=120)
    r.raise_for_status()
    return r.content

VIS_SAR_BEFORE = {"bands": ["before_vv"], "min": -25, "max": 0}
VIS_SAR_AFTER = {"bands": ["after_vv"], "min": -25, "max": 0}
# flood mask: image.select("flooded").selfMask().visualize(palette=["ff2d2d"]) then getThumbURL without min/max
```
Shares (`flooded_pct`) are computed by FR21 against PostGIS `ST_Area(geom::geography)`. Note: with one band, `reduceRegions` + `Reducer.sum()` names the output property `sum` (verify).

### 6. Terrain (`app/services/ingestion/terrain.py`)

```python
def compute_terrain(fc: ee.FeatureCollection) -> list[dict]:
    dem = ee.Image(settings.SRTM_COLLECTION).select("elevation")
    img = dem.rename("elev").addBands(ee.Terrain.slope(dem).rename("slope"))
    out = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=90, tileScale=4)
    return [{"tehsil_pcode": f["properties"]["pcode"],
             "elevation_mean_m": round(f["properties"]["elev"], 1),
             "slope_mean_deg": round(f["properties"]["slope"], 2)}
            for f in _call(out.getInfo)["features"]]
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `GEE_SERVICE_ACCOUNT` / `GEE_KEY_FILE` | — / `secrets/gee-key.json` | Foundation keys |
| `GEE_PROJECT` | — | Cloud project registered for EE (new key) |
| `GEE_HIGH_VOLUME` | `true` | High-volume endpoint for batch jobs (verify quota) |
| `IMERG_COLLECTION` / `IMERG_BAND` | `NASA/GPM_L3/IMERG_V07` / `precipitation` | Verify in catalog |
| `IMERG_REDUCE_SCALE_M` | 5000 | |
| `IMERG_LOOKBACK_DAYS` | 3 | |
| `IMERG_EXPECTED_LAG_DAYS` | 2 | |
| `IMERG_MIN_IMAGES_PARTIAL` | 40 | of 48 |
| `IMERG_MAX_COPY_FORWARD_DAYS` | 3 | |
| `REPLAY_IMERG_LAG_DAYS` | 1 | |
| `EE_TEHSIL_BATCH` / `EE_SIMPLIFY_DEG` | 300 / 0.002 | |
| `S1_COLLECTION` | `COPERNICUS/S1_GRD` | |
| `S1_DIFF_DB` / `S1_AFTER_MAX_DB` | −3.0 / −15.0 | Tune on Dadu 2022 |
| `S1_SPECKLE_RADIUS_M` | 50 | |
| `JRC_COLLECTION` / `JRC_PERM_OCCURRENCE` | `JRC/GSW1_4/GlobalSurfaceWater` / 80 | Verify version (1_4 current at writing) |
| `SRTM_COLLECTION` | `USGS/SRTMGL1_003` | |
| `S1_MAX_SLOPE_DEG` / `S1_MIN_CONNECTED_PX` / `S1_AREA_SCALE_M` | 5 / 8 / 30 | |
| `THUMB_DIMENSIONS` | 1024 | |

## Commands

```bash
pip install -e ".[dev]"
python scripts/ee_smoke_test.py
pytest -q tests/unit/ingestion/test_imerg_math.py tests/unit/integrations/test_earth_engine.py tests/integration/ingestion -k "imerg or terrain"
pytest -q -m ee tests/live/test_ee_live.py              # needs credentials
python scripts/compute_tehsil_terrain.py --provinces SINDH KP
python scripts/cache_replay_imerg.py --scenario sindh-2022
python scripts/cache_replay_imerg.py --scenario buner-2025
python scripts/cache_replay_imerg.py --scenario control-2021
curl -X POST localhost:8000/api/v1/ingestion/run -H "Authorization: Bearer $ADMIN" \
     -H "Content-Type: application/json" -d '{"source":"GPM_IMERG"}'
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1 | `test_imerg_ingest.py::test_rows_per_tehsil_per_day` |
| US1-2 | `test_imerg_ingest.py::test_incomplete_recent_day_skipped` |
| US1-3 | `test_imerg_ingest.py::test_centroid_fallback` |
| US1-4 | `test_imerg_ingest.py::test_idempotent` |
| US1-5 | `test_imerg_ingest.py::test_step_never_raises` |
| US2-1 | Manual: CSVs committed, row counts = tehsils × days |
| US2-2 | `test_imerg_replay.py` |
| US3-1..2 | `test_terrain_seed.py`; manual run of `compute_tehsil_terrain.py` |
| US4-1..4 | `test_earth_engine.py` (fake), `test_ee_live.py -k s1` (live) |
| US5-1..2 | `test_imerg_ingest.py -k "outage or recovery"` |
| SC-001 | T030 timing note |
| SC-003 | `test_imerg_math.py` + live parity test |
| SC-006 | `test_ee_live.py::test_s1_dadu_2022` |

## Demo notes

- IMERG drives the tehsil colours on the risk map during the Sindh 2022 replay, and the Buner 2025 flash trigger (3-hour burst around 15 Aug 2025).
- Judge question "Is the data detailed enough?": answer with the tehsil map coloured from IMERG while NASA POWER is per district (document p. 22).
- The S1 maps on stage come from FR21's precomputed files, built with these primitives.

## Pitfalls

- `ee.Initialize()` must run once per process, and before any `ee.*` object is built (including module-level constants). Keep dataset objects inside functions.
- Server-side `map` lambdas must use `ee.Number`, not Python ints; Python `if` on EE objects is a silent bug.
- `getInfo()` blocks and is quota-limited; keep the job sequential and never call it inside a loop over tehsils.
- `toList(48)` silently truncates if a day ever has more images (shouldn't for IMERG; assert `n <= 48`).
- IMERG `precipitation` is a **rate** (mm/hr). Forgetting × 0.5 doubles every total.
- Sentinel-1 GRD in EE is already in dB; do not apply `log10` again. Thresholds assume dB.
- In 2022 only Sentinel-1A was flying (S1B failed Dec 2021), so revisit is ~12 days; windows must be wide enough. Sentinel-1C (2025) improves Buner coverage (verify acquisitions with the smoke test).
- Thumbnails cap at roughly 32 MB / large dimensions; 1024 px is plenty. Download immediately (URLs expire).
- Never commit the service-account JSON. Rotate the key if it leaks.
