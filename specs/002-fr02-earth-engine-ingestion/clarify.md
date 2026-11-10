# Clarifications: FR2 Earth Engine Ingestion

## Clarifications

### Session 2026-11-10

- Q: FR2's text says "Sentinel 1 flood maps" and it is MVP, but FR21 (satellite confirmation) is roadmap. Who builds what? → A: D1 resolves it. FR2 owns the Earth Engine logic (S1 change detection, JRC mask, per-tehsil area, thumbnails). FR21 owns the precompute script, the `SatelliteFloodMap` rows and the read endpoint (MVP per D1), and the live verification job (roadmap). FR2 itself does not ingest S1 daily in the MVP.
- Q: Which IMERG collection and band? → A: `NASA/GPM_L3/IMERG_V07`, band `precipitation` (mm/hr, half-hourly). **Verify** in the EE catalog: V06 used `precipitationCal`, and the run type (Early/Late/Final) of the EE collection decides live latency. Both ID and band are settings (`IMERG_COLLECTION`, `IMERG_BAND`).
- Q: How are half-hourly rates turned into mm? → A: Each image is a rate in mm/hr over 30 min, so accumulation = rate × 0.5. Daily total = Σ(48 images) × 0.5.
- Q: Exactly what is "max 3-hour intensity"? → A: Per pixel, sum each run of 6 consecutive half-hours (× 0.5), take the max over the day's 43 windows, then take the tehsil mean of that per-pixel maximum. It is computed server-side in one image, so one `reduceRegions` call returns both bands. Alternative "max of the tehsil-mean series" needs 48 reductions per day; the per-pixel version also better reflects local bursts that cause flash floods.
- Q: Which reducer and scale? → A: `ee.Reducer.mean()` at `scale=5000` m (IMERG native is 0.1° ≈ 11 km). A finer scale than native makes EE resample, so tehsils smaller than a cell still get a value (pixel centres would otherwise miss them). If a tehsil still returns null, fall back to sampling the tehsil centroid.
- Q: What counts as a "complete" day? → A: 48 images in the UTC day. Days newer than `today − IMERG_EXPECTED_LAG_DAYS` (2) that are incomplete are skipped. Older days with 40–47 images are stored with `quality_flags=["imerg_partial:N/48"]`. Older days with < 40 images go down the stale path.
- Q: Why is the replay lag (1) different from the live expected lag (2)? → A: The replay lag models what a decision-maker would have on day D (near-real-time IMERG for D−1). The live expected lag is a tolerance for when to call data overdue. They answer different questions.
- Q: Should IMERG rows set `district_id` too? → A: Yes, the tehsil's district, as well as `tehsil_id`. It makes district-level queries simple. `source=GPM_IMERG` keeps it apart from NASA POWER rows.
- Q: How do boundaries get to Earth Engine? → A: Build an `ee.FeatureCollection` from PostGIS with `ST_SimplifyPreserveTopology(geom, 0.002)` (~200 m) and a `pcode` property, cached per process. 60–260 simplified polygons fit in one request (EE request limit ~10 MB). Uploading an EE table asset is an optional optimisation for the national rollout.
- Q: Auth? → A: `ee.ServiceAccountCredentials(GEE_SERVICE_ACCOUNT, GEE_KEY_FILE)` then `ee.Initialize(credentials, project=GEE_PROJECT, opt_url="https://earthengine-highvolume.googleapis.com")`. The high-volume endpoint suits automated jobs (verify quota policy for noncommercial projects).
- Q: Which Sentinel-1 rule for new water? → A: Filter `COPERNICUS/S1_GRD` to `instrumentMode=IW`, VV present, optionally one `orbitProperties_pass`. Before = median of a dry window; after = latest-on-top mosaic of the after window. Smooth both with a 50 m focal median. Flooded where `after − before < −3 dB` **and** `after < −15 dB`. Remove JRC `occurrence > 80`, slopes > 5° (from SRTM) and clusters < 8 connected pixels. Thresholds are settings and need tuning on Dadu 2022 (verify).
- Q: Before window for the event maps? → A: Dry pre-monsoon window, not the image just before the trigger, because July 2022 was already wet in Sindh. Exact windows are FR21's (`data/seeds/satellite_events.yaml`).
- Q: Elevation scale? → A: `USGS/SRTMGL1_003` band `elevation`, `ee.Reducer.mean()` at `scale=90` with `tileScale=4`. Slope from `ee.Terrain.slope()`. Computed once and saved to `data/seeds/tehsil_terrain.csv`.
- Q: Area for flooded share? → A: `flooded × ee.Image.pixelArea()` summed at `scale=30`, `tileScale=8`; tehsil area from PostGIS `ST_Area(geom::geography)`. Share = flooded / area × 100.
- Q: Thumbnails? → A: `image.getThumbURL({region, dimensions: 1024, format: "png", crs: "EPSG:3857"})` then download the bytes immediately (URLs expire). Web Mercator lets the frontend place the PNG with a Leaflet image overlay on the bounding box.

## Resolved by constitution decisions

- **D1**: S1 before/after maps for replay events are precomputed (FR21 builds them with FR2's primitives). Live S1 confirmation is roadmap.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Is the EE `IMERG_V07` collection Early/Late (hours) or Final (months) latency? | Use it as configured; verify on day 1 with `scripts/ee_smoke_test.py`. If it lags by months, live IMERG goes stale and FR3 falls back to NASA POWER + forecast rain; a NASA PPS Early-run GeoTIFF client is roadmap | Live tehsil rainfall freshness; replay unaffected | Satellite & GIS |
| 2 | Max 3-hour definition | Tehsil mean of per-pixel max rolling 3 h | FR4 flash thresholds must be calibrated on the same definition | Satellite & GIS with Data & ML |
| 3 | S1 thresholds (−3 dB drop, −15 dB absolute, slope 5°, 8 px) | As listed, tuned on Dadu 2022 | Flooded km² values in FR21 maps | Satellite & GIS |
| 4 | IMERG expected lag and partial-day minimum | 2 days; ≥ 40/48 images | Stale banner frequency | Satellite & GIS |
| 5 | Add `Tehsil.slope_mean_deg` | Proposed in plan.md | Without it, FR4 uses elevation only | Foundation owner |
| 6 | Batch size for national rollout | 300 tehsils per `reduceRegions` call | Only matters beyond the pilot | Satellite & GIS |
