# Clarifications: FR1 NASA POWER Ingestion

## Clarifications

### Session 2026-11-10

- Q: Which NASA POWER endpoint and parameters? → A: The daily point API `GET https://power.larc.nasa.gov/api/temporal/daily/point` with `parameters=PRECTOTCORR,T2M,GWETROOT,GWETTOP`, `community=AG`, `format=JSON`. `PRECTOTCORR` is bias-corrected precipitation (mm/day), the best POWER rain product. `community=AG` matches agro-climatology units (mm/day, °C).
- Q: One point per district, or per tehsil? → A: One point per district centroid (guide, data-model `District.centroid_lat/lon`). POWER's grid (~0.5° × 0.625°) is coarser than most tehsils, so per-tehsil queries would repeat the same cell. Tehsil detail comes from IMERG (FR2).
- Q: Which soil wetness goes in `soil_wetness`? → A: `GWETROOT` (root zone, 0–100 cm). It reflects saturation that matters for slow-onset floods. `GWETTOP` (0–5 cm) is also stored, in a proposed `soil_wetness_top` column, because it reacts faster for flash mode.
- Q: How is a "missing" value detected? → A: Any value equal to the header `fill_value` (default `-999`), or ≤ −998.5 to be safe with float formatting.
- Q: What counts as "wrong units" (UC03-5)? → A: The response header `parameters.<P>.units` must match the expected units. A mismatch rejects that district's response as an error (no guessing). Inside the values, soil wetness between 1 and 100 is the one known unit slip (percent instead of fraction) and is converted with a quality note. Everything else out of range is rejected as missing.
- Q: Valid ranges? → A: Rainfall 0–600 mm/day (Pakistan's highest recorded 24-hour totals are below this), temperature −30 to 55 °C, soil wetness 0–1. Rainfall in [−0.5, 0) is clamped to 0. Ranges are settings.
- Q: POWER publishes with a delay. When is data "stale" rather than just "not yet published"? → A: Days newer than `today − POWER_EXPECTED_LAG_DAYS` (default 3) are not yet due and are simply absent. Only days at or before that line that are still missing are overdue, and only they go down the stale path. This avoids a permanent stale banner caused by normal latency.
- Q: What does "use the last valid data" mean for rainfall? → A: Copy the whole last valid observation (rain, temperature, soil wetness) forward into each overdue day, flagged `is_stale=true`, `stale_since=<last valid date>`, for at most 3 days. Copying rain forward keeps risk elevated during an outage in the rainy season, which errs on the side of anticipation (Watch releases only 10%, a low-regret action). Copying zero rain would hide risk silently. The cap stops a single wet day being repeated for a week. FR3 sees the stale flag and decides how much to trust it.
- Q: How is `DataSourceStatus.last_valid_date` defined when districts differ? → A: The earliest of the per-district latest fresh dates across active districts. It is conservative: the banner shows the oldest data any district is relying on.
- Q: When is the source "unhealthy"? → A: When any active district needed copy-forward or failed to fetch in this run. One admin alert is sent only on the healthy → unhealthy transition. Recovery is recorded on the reverse transition.
- Q: Who is "the admin" for the alert? → A: Users with role `ADMIN`. The data model's `Alert.recipient_type` has no `ADMIN` value yet, so it is proposed in plan.md. Until merged, the alert is logged at ERROR level and visible via `GET /api/v1/health/data-sources`.
- Q: Should the run be one transaction? → A: Yes. Fetch everything over the network first, then write all rows, the status and the audit entries in one transaction. 12 districts × 10 days is 120 rows, so it's cheap and atomic, and it keeps the audit entry in the same transaction as the data (Principle III).
- Q: How does FR1 hand over to FR3 (UC03-6)? → A: FR1 registers `register_step(10, "ingest.nasa_power", run_nasa_power_step)`. FR2 registers order 20, FR3 order 30. The step never raises; it catches, flags and returns, so the pipeline always reaches FR3.
- Q: How does replay avoid lookahead? → A: The cached client truncates to dates ≤ `replay_today − REPLAY_POWER_LAG_DAYS` (default 2, which mimics real latency). Replay rows carry `scenario_id`. `DataSourceStatus` is a live-only table, so replay leaves it alone and reports staleness only in the run summary and audit.
- Q: What does the cache contain? → A: The raw POWER JSON per district (`data/replay/<key>/nasa_power/<district_pcode>.json`), covering the scenario window plus `REPLAY_SPINUP_DAYS=30` before the start (FR4 needs 30-day sums). Raw JSON means replay goes through the same parser and validation as live.
- Q: Calendar day: UTC or Pakistan time? → A: UTC (`time-standard=UTC`), matching the constitution and IMERG (FR2). The 5-hour offset is small relative to daily totals.
- Q: Where do district-level vulnerability indicators come from? → A: World Bank's API (`api.worldbank.org/v2`) only gives national values for Pakistan, so the MVP uses a curated CSV (`data/seeds/context_indicators.csv`) built from EasyData / PSLM 2019-20 / UNDP MPI district tables, each row labelled with source and period. The World Bank client refreshes national reference values used for sanity checks. A live EasyData API client is roadmap.
- Q: How is `vulnerability_weight` computed? → A: `Σ wᵢ · normᵢ / Σ wᵢ`, where `normᵢ` is min-max normalisation of indicator *i* across all active districts, inverted for "good" indicators (e.g. literacy). Default weights: `poverty_headcount` 0.5, `housing_kutcha_pct` 0.3, `literacy_rate` 0.2 (inverted). Missing values use the province median and set `imputed=true`.

## Resolved by constitution decisions

- None of D1–D6 apply directly to FR1. Principles VII (degrade gracefully), IX (config over code) and III (audit) shape the design.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Real POWER latency for `PRECTOTCORR` | `POWER_EXPECTED_LAG_DAYS=3` (verify against a week of live calls before the demo) | Too low → false stale banners; too high → slow detection of outages | Data & ML |
| 2 | Copy-forward rainfall during an outage: last value or zero? | Last valid value, max 3 days | Zero would under-forecast during outages; longer caps risk repeated heavy rain | Data & ML with FR3 owner |
| 3 | Replay simulated lag | `REPLAY_POWER_LAG_DAYS=2` | Larger lag shortens lead time in the demo; 0 would be lookahead | Data & ML with FR5 owner |
| 4 | Vulnerability indicators and weights | poverty 0.5, kutcha housing 0.3, literacy 0.2 (inverted) | Changes the district weights, FR3 features and FR11 fairness expectations | PDMA domain lead / Data & ML |
| 5 | Indicator source for districts | Curated CSV from EasyData / PSLM / MPI, labelled per row | A live API would need a new client | Data & ML |
| 6 | Add `ADMIN` to `Alert.recipient_type` and `DATA_SOURCE_DOWN` to `Alert.purpose` | Proposed in plan.md; log-only until merged | Without it the admin alert is not on WhatsApp | Foundation owner |
| 7 | Demo the stale banner during replay? | Supported via optional `outages.json` in the cache folder; off by default | Adds 10 s to the demo script | Frontend & pitch |
