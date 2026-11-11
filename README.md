# Aab o Hisaab AI: backend

Specs live in [`specs/`](specs/README.md); the constitution is in `.specify/memory/constitution.md`.

## Built so far

| Part | Status |
|---|---|
| Foundation (minimal slice) | Settings, Postgres + PostGIS models and migration, JWT login with roles, scenario/clock helpers, pipeline step registry, stub audit and alert services |
| FR1 NASA POWER ingestion | Daily ingest, validation, stale path with admin alert, replay cache reader, API, backfill and cache script, context indicators and vulnerability weight |
| FR2 Earth Engine ingestion | IMERG daily total + max 3-hour per tehsil, live (Earth Engine) and replay (CSV) sources, stale path, terrain and Sentinel-1 helpers, scripts |
| FR3 Flood risk score | Open-Meteo forecast rain (+ stale shift), feature library, rule-based fallback scorer, model loader (no trained model yet), tehsil + district scores, template explanations, read APIs |
| FR4 Flood modes | Validated SLOW_ONSET / FLASH YAML definitions, province seed, effective mode with replay snapshot, audited mode change API, threshold report |
| FR5 Event replay | Replay engine (start, step, seek, reset, state, timeline, autoplay), cache manifests, backtest evaluator, NFR3 backtests (`pytest -m backtest`) |
| FR6 Fund and tier config | Fund settings API with full validation, one audit per change, optimistic locking, copy drafts, shared tier rule used by scoring |

## Run locally

```bash
docker compose up -d db                      # Postgres 16 + PostGIS (also creates aab_test)
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # add ,ee for Earth Engine
cp .env.example .env
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m scripts.seed          # districts, tehsils, demo users (password: demo), scenarios
.venv/Scripts/uvicorn app.main:app --reload
.venv/Scripts/python -m pytest -q             # unit + DB tests (uses aab_test)
```

## Data scripts

```bash
python -m scripts.ingest_nasa_power --all-scenarios --to-cache     # replay caches (already committed)
python -m scripts.ingest_nasa_power --start 2015-01-01 --end 2025-12-31   # backfill for FR3 training
python -m scripts.ee_smoke_test                                    # needs Earth Engine credentials
python -m scripts.cache_replay_imerg --scenario sindh-2022          # needs Earth Engine + tehsil boundaries
python -m scripts.compute_tehsil_terrain                            # needs Earth Engine + tehsil boundaries
```

## Backtest status (`pytest -m backtest`, reports in `reports/backtest/`)

| Scenario | Result | Note |
|---|---|---|
| sindh-2022 | Pass | Dadu at Watch from 1 Aug (21 days before onset); July 2022 rain was already extreme, so the rule saturates early |
| control-2021 | Pass | Highest district score 23, never reaches Watch (40) |
| buner-2025 | **Fail** | Peak score 24. NASA POWER (about 50 km grid) shows only 38.5 mm on 14 Aug; flash mode needs IMERG 3-hour bursts and SRTM slope, which need Earth Engine |

All scores come from the rule-based fallback (no trained model yet) and replay forecast rain is a labelled perfect-forecast proxy from observed rain. Until FR7 exists, backtests judge on tier crossings, not fund releases.

## Known gaps (before the pilot)

- District and tehsil **P-codes and centroids are provisional** (`data/seeds/districts.yaml`). Load OCHA COD-AB boundaries, then rebuild the replay caches.
- **Tehsil boundaries are not loaded**, so live IMERG and terrain cannot run yet (they need polygons).
- **Earth Engine credentials** are not configured; FR2's live path is untested against real Earth Engine. The IMERG replay caches (`imerg_daily.csv`) are not built yet.
- `data/seeds/context_indicators.csv` is empty: real district indicators (PSLM/MICS) still need curating; until then every district's vulnerability weight is a neutral 0.5.
- No trained flood model: every score uses the published fallback rule (`is_fallback=true`). Training needs a labelled flood-event file and IMERG history.
- FR7 (releases), FR8 (approvals) and FR17 (payments) are not built: replays create scores and scenario funds but no releases or approvals.
- Audit entries are written by a stub without the real hash chain (FR20) and alerts are only logged (FR14).
