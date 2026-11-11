"""PDMA weather, flood and hazard forecasting.

Prediction engine: the team's trained C02 flood model (aab_o_hisaab_ml/models/flood_model.joblib, a random forest).
It gives, for each district and day t, the probability of a qualifying flood on any of days t+1..t+7. Features are
rebuilt here exactly as in aab_o_hisaab_ml/src/data/s05_build_dataset.py from NASA POWER daily data, using the
district climatology from the training years (2000-2014) only.

The map score is "Model Risk Score (%)" = model probability x 100. Random-forest probabilities are NOT calibrated,
so the score is a relative risk score, not the chance of a flood.

Supporting sources (shown separately, never mixed into the model score):
  - Open-Meteo forecast: 7-day weather per district (not NASA).
  - NASA EONET: observed natural-hazard events (floods, landslides, storms, wildfires) near Pakistan.
Sources that need credentials we do not have (IMERG via Earthdata, FIRMS MAP_KEY) are reported as unavailable.
"""
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape
from sqlalchemy import delete, func, select

from model.forecast_model import (
    DistrictRiskAssessment,
    ForecastDistrict,
    ForecastRun,
    HazardEvent,
    HazardForecast,
    HazardObservation,
)

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
ML = ROOT / "aab_o_hisaab_ml"
GEO = ROOT / "data" / "geo"
CACHE = ROOT / "storage" / "nasa_power"
PKT = timedelta(hours=5)

POWER_API = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = ["PRECTOTCORR", "T2M", "T2M_MAX", "GWETTOP", "GWETROOT", "GWETPROF", "RH2M"]
POWER_SOURCE = "NASA POWER (MERRA-2)"
UNITS = {"PRECTOTCORR": "mm/day", "T2M": "°C", "T2M_MAX": "°C", "GWETTOP": "fraction", "GWETROOT": "fraction",
         "GWETPROF": "fraction", "RH2M": "%"}
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OM_SOURCE = "Open-Meteo forecast"
OM_DAILY = {"precipitation_sum": "mm", "temperature_2m_max": "°C", "temperature_2m_min": "°C",
            "wind_speed_10m_max": "km/h", "wind_gusts_10m_max": "km/h", "relative_humidity_2m_mean": "%"}
OM_CURRENT = {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"}
EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"
EONET_SOURCE = "NASA EONET"
PAK_BBOX = "60.5,37.2,77.9,23.5"
HISTORY_DAYS = 70          # >= 30-day rolling window + 7-day change + POWER latency
STALE_AFTER_DAYS = 7       # NASA POWER normally lags 2-4 days
HEAT_C = 45.0              # forecast max temperature counted as extreme heat
CLIM_START, CLIM_END = "20000101", "20141231"
TRAIN_END = 2014


# Risk bands (inclusive lower bound, exclusive upper) ------------------------------------------------
BANDS = (("immediate", 90.0), ("warning", 80.0), ("watch", 60.0), ("low", 0.0))
LABELS = {"low": "Low / Normal", "watch": "Watch", "warning": "Warning", "immediate": "Immediate Action",
          "insufficient": "Insufficient Data"}


def category(score):
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "insufficient"
    for name, low in BANDS:
        if score >= low:
            return name
    return "low"


def now_utc():
    return datetime.utcnow()


def today_pkt():
    return (datetime.utcnow() + PKT).date()


# Model --------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_model():
    import joblib

    bundle = joblib.load(ML / "models" / "flood_model.joblib")
    return bundle


def model_version():
    b = load_model()
    return f"{b['name']}@train{b.get('train_years', '')}"[:60]


@lru_cache(maxsize=1)
def district_shapes():
    gj = json.loads((GEO / "pak_districts.geojson").read_text())
    return [(f["properties"]["pcode"], shape(f["geometry"])) for f in gj["features"]]


def district_at(lat, lon):
    p = Point(lon, lat)
    for pcode, geom in district_shapes():
        if geom.contains(p):
            return pcode
    return None


# Runs ---------------------------------------------------------------------------------------------
class Run:
    def __init__(self, db, kind, trigger="schedule"):
        self.db = db
        self.row = ForecastRun(kind=kind, status="running", trigger=trigger, started_at=now_utc(), records=0)
        db.add(self.row)
        db.commit()

    def done(self, records, status="success", error=None):
        self.row.records, self.row.status = records, status
        self.row.error = (error or None) and str(error)[:1000]
        self.row.finished_at = now_utc()
        self.db.commit()


# Districts and climatology -----------------------------------------------------------------------
def seed_districts(db):
    """Create forecast_districts from the ML project's district table (idempotent)."""
    if db.scalar(select(func.count()).select_from(ForecastDistrict)):
        return 0
    dist = pd.read_csv(ML / "data" / "interim" / "districts.csv")
    ev = pd.read_csv(ML / "data" / "interim" / "district_flood_events.csv", parse_dates=["begin"])
    hist = ev[ev["begin"].dt.year <= TRAIN_END].groupby("district_pcode").size().to_dict()
    gj = json.loads((GEO / "pak_districts.geojson").read_text())
    prov = {f["properties"]["pcode"]: f["properties"]["province_pcode"] for f in gj["features"]}
    for r in dist.itertuples():
        db.add(ForecastDistrict(pcode=r.district_pcode, name=r.district_name, province=r.province,
                                province_pcode=prov.get(r.district_pcode), lat=float(r.lat), lon=float(r.lon),
                                area_km2=float(r.area_km2), flood_mode=r.flood_mode,
                                hist_floods_train=int(hist.get(r.district_pcode, 0))))
    db.commit()
    return len(dist)


def cells():
    return pd.read_csv(ML / "data" / "interim" / "district_cells.csv")


def _power(lat, lon, start, end, timeout=120):
    r = requests.get(POWER_API, timeout=timeout, params={
        "parameters": ",".join(POWER_PARAMS), "community": "AG", "latitude": lat, "longitude": lon,
        "start": start, "end": end, "format": "JSON", "time-standard": "UTC"})
    r.raise_for_status()
    p = r.json()["properties"]["parameter"]
    df = pd.DataFrame({k: pd.Series(v) for k, v in p.items()})
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.replace(-999.0, np.nan)
    df["lat"], df["lon"] = lat, lon
    return df.rename_axis("date").reset_index()


def _fetch_cells(start, end, cache_prefix=None, workers=6):
    unique = cells()[["lat", "lon"]].drop_duplicates().values.tolist()
    out, failed = [], 0

    def one(cell):
        lat, lon = cell
        path = CACHE / f"{cache_prefix}_{lat:.3f}_{lon:.3f}.parquet" if cache_prefix else None
        if path and path.exists():
            return pd.read_parquet(path)
        for attempt in range(3):
            try:
                df = _power(lat, lon, start, end)
                if path:
                    CACHE.mkdir(parents=True, exist_ok=True)
                    df.to_parquet(path, index=False)
                return df
            except Exception as e:  # noqa: BLE001
                log.warning("NASA POWER cell %s,%s attempt %s failed: %s", lat, lon, attempt + 1, e)
        return None

    with ThreadPoolExecutor(workers) as ex:
        for df in ex.map(one, unique):
            if df is None:
                failed += 1
            else:
                out.append(df)
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame()), failed, len(unique)


def _district_daily(cell_df):
    merged = cells().merge(cell_df, on=["lat", "lon"])
    return merged.groupby(["district_pcode", "date"])[POWER_PARAMS].mean().reset_index()


def build_climatology(db, trigger="manual"):
    """One-off: 2000-2014 NASA POWER per grid cell -> per-district training-period thresholds used by the model.
    Cell downloads are cached in storage/nasa_power, so an interrupted run resumes."""
    seed_districts(db)
    run = Run(db, "climatology", trigger)
    try:
        raw, failed, total = _fetch_cells(CLIM_START, CLIM_END, cache_prefix="clim", workers=4)
        if failed:
            run.done(total - failed, "failed", f"{failed} of {total} grid cells failed; run again to resume.")
            return run.row
        w = _district_daily(raw).sort_values(["district_pcode", "date"])
        g = w.groupby("district_pcode", group_keys=False)
        w["rain_7d"] = g["PRECTOTCORR"].transform(lambda s: s.clip(lower=0).rolling(7, min_periods=7).sum())
        w["rain_30d"] = g["PRECTOTCORR"].transform(lambda s: s.clip(lower=0).rolling(30, min_periods=30).sum())
        clim = w.groupby("district_pcode").agg(
            gwt_q99=("GWETTOP", lambda s: float(np.quantile(s.dropna(), 0.99))),
            gwr_q99=("GWETROOT", lambda s: float(np.quantile(s.dropna(), 0.99))),
            gwp_q99=("GWETPROF", lambda s: float(np.quantile(s.dropna(), 0.99))),
            clim_rain_7d=("rain_7d", "mean"), clim_rain_30d=("rain_30d", "mean"))
        n = 0
        for d in db.scalars(select(ForecastDistrict)):
            if d.pcode in clim.index:
                c = clim.loc[d.pcode]
                d.gwt_q99, d.gwr_q99, d.gwp_q99 = float(c.gwt_q99), float(c.gwr_q99), float(c.gwp_q99)
                d.clim_rain_7d, d.clim_rain_30d = float(c.clim_rain_7d), float(c.clim_rain_30d)
                d.climatology_at = now_utc()
                n += 1
        db.commit()
        run.done(n)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("Climatology build failed.")
        run.done(0, "failed", e)
    return run.row


# Live NASA POWER observations ---------------------------------------------------------------------
def refresh_observations(db, trigger="schedule"):
    seed_districts(db)
    run = Run(db, "nasa_power", trigger)
    try:
        end = datetime.utcnow().date()
        start = end - timedelta(days=HISTORY_DAYS)
        raw, failed, total = _fetch_cells(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if raw.empty:
            run.done(0, "failed", f"NASA POWER returned no data ({failed} of {total} cells failed).")
            return run.row
        daily = _district_daily(raw)
        long = daily.melt(id_vars=["district_pcode", "date"], var_name="variable", value_name="value").dropna()
        retrieved = now_utc()
        # Replace the window atomically: NASA revises recent days, and the unique key forbids duplicates.
        db.execute(delete(HazardObservation).where(HazardObservation.source == POWER_SOURCE,
                                                   HazardObservation.observation_date >= start))
        db.bulk_insert_mappings(HazardObservation, [
            {"district_pcode": r.district_pcode, "variable": r.variable, "value": float(r.value),
             "unit": UNITS[r.variable], "source": POWER_SOURCE, "observation_date": r.date.date(),
             "retrieved_at": retrieved} for r in long.itertuples()])
        db.commit()
        run.done(len(long), "partial" if failed else "success",
                 f"{failed} of {total} grid cells failed; districts using them may lack data." if failed else None)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("NASA POWER refresh failed.")
        run.done(0, "failed", e)
    return run.row


# Feature building (mirrors s05_build_dataset.py) -------------------------------------------------
def build_features(obs: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    w = obs.pivot_table(index=["district_pcode", "date"], columns="variable", values="value").reset_index()
    for c in POWER_PARAMS:
        if c not in w:
            w[c] = np.nan
    w["date"] = pd.to_datetime(w["date"])
    # Complete daily calendar per district: a missing day stays NaN, so rolling windows never bridge gaps.
    full = pd.MultiIndex.from_product([sorted(w["district_pcode"].unique()),
                                       pd.date_range(w["date"].min(), w["date"].max(), freq="D")],
                                      names=["district_pcode", "date"])
    w = w.set_index(["district_pcode", "date"]).reindex(full).reset_index()
    w = w.merge(districts, on="district_pcode", how="inner")
    w = w.sort_values(["district_pcode", "date"]).reset_index(drop=True)
    g = w.groupby("district_pcode", group_keys=False)
    for k in (1, 3, 7, 14, 30):
        w[f"rain_{k}d"] = g["PRECTOTCORR"].transform(lambda s, k=k: s.clip(lower=0).rolling(k, min_periods=k).sum())
    w["rain_max1d_7d"] = g["PRECTOTCORR"].transform(lambda s: s.rolling(7, min_periods=7).max())
    w["rain_days10_7d"] = g["PRECTOTCORR"].transform(
        lambda s: (s > 10).astype(float).where(s.notna()).rolling(7, min_periods=7).sum())
    w["rain_3d_over_30d"] = w["rain_3d"] / (w["rain_30d"] + 1.0)
    for c in ("GWETTOP", "GWETROOT", "GWETPROF", "T2M", "T2M_MAX", "RH2M"):
        w[c.lower()] = w[c]
    w["gwettop_chg_3d"] = w["GWETTOP"] - g["GWETTOP"].shift(3)
    w["gwettop_chg_7d"] = w["GWETTOP"] - g["GWETTOP"].shift(7)
    w["gwetroot_chg_7d"] = w["GWETROOT"] - g["GWETROOT"].shift(7)
    w["gwetprof_chg_7d"] = w["GWETPROF"] - g["GWETPROF"].shift(7)
    w["t2m_max_7d"] = g["T2M_MAX"].transform(lambda s: s.rolling(7, min_periods=7).mean())
    w["t2m_max_chg_7d"] = w["T2M_MAX"] - g["T2M_MAX"].shift(7)
    doy = w["date"].dt.day_of_year
    w["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    w["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    w["is_flash_mode"] = (w["flood_mode"] == "flash").astype(int)
    p = w.groupby(["province", "date"]).agg(prov_rain_3d=("rain_3d", "mean"), prov_rain_7d=("rain_7d", "mean"),
                                           prov_gwettop=("gwettop", "mean"), prov_gwetroot=("gwetroot", "mean"),
                                           prov_gwetprof=("gwetprof", "mean")).reset_index()
    w = w.merge(p, on=["province", "date"], how="left")
    w["rain_7d_anom"] = w["rain_7d"] / (w["clim_rain_7d"] + 1.0)
    w["rain_30d_anom"] = w["rain_30d"] / (w["clim_rain_30d"] + 1.0)
    w["gwetroot_anom"] = w["gwetroot"] - w["gwr_q99"]
    w["gwettop_rel_q99"] = w["gwettop"] / np.maximum(w["gwt_q99"], 0.75)
    w["gwetroot_rel_q99"] = w["gwetroot"] / np.maximum(w["gwr_q99"], 0.72)
    w["gwetprof_rel_q99"] = w["gwetprof"] / w["gwp_q99"]
    return w


INDICATORS = (("rain_7d_anom", "7-day rainfall vs district normal"),
              ("gwettop_rel_q99", "Surface soil saturation"),
              ("gwetroot_rel_q99", "Root-zone soil saturation"),
              ("gwetprof_rel_q99", "Deep soil saturation"))


def score_districts(db, trigger="schedule"):
    """Score every district on its latest complete observation date with the trained model."""
    run = Run(db, "model", trigger)
    try:
        bundle = load_model()
        features = bundle["features"]
        dist = pd.read_sql(select(ForecastDistrict), db.bind).rename(columns={"pcode": "district_pcode"})
        obs = pd.read_sql(select(HazardObservation.district_pcode, HazardObservation.variable, HazardObservation.value,
                                 HazardObservation.observation_date.label("date"))
                          .where(HazardObservation.source == POWER_SOURCE,
                                 HazardObservation.observation_date >= datetime.utcnow().date() - timedelta(days=HISTORY_DAYS)),
                          db.bind)
        calculated = now_utc()
        version = model_version()
        rows = []
        feats = build_features(obs, dist) if not obs.empty else pd.DataFrame(columns=["district_pcode", "date"])
        for d in dist.itertuples():
            f = feats[feats["district_pcode"] == d.district_pcode]
            complete = f.dropna(subset=features)
            clim_missing = d.gwt_q99 is None or pd.isna(d.gwt_q99)
            if clim_missing or complete.empty:
                have = f[features].notna().mean(axis=1).max() if not f.empty else 0.0
                missing = ["district climatology (2000-2014)"] if clim_missing else []
                if f.empty:
                    missing.append("NASA POWER observations")
                elif complete.empty:
                    missing += sorted({c for c in features if f[c].iloc[-1:].isna().any()})[:8]
                rows.append(dict(district_pcode=d.district_pcode, as_of_date=None, probability=None, risk_score=None,
                                 risk_category="insufficient", model_version=version, primary_indicator=None,
                                 data_completeness=round(float(have or 0), 3), calculated_at=calculated, run_id=run.row.id,
                                 calculation_details={"missing": missing}))
                continue
            last = complete.iloc[-1]
            prob = float(bundle["model"].predict_proba(complete.iloc[[-1]][features])[:, 1][0])
            score = round(min(max(prob * 100.0, 0.0), 100.0), 1)
            main = max(INDICATORS, key=lambda kv: float(last[kv[0]]))
            rows.append(dict(
                district_pcode=d.district_pcode, as_of_date=last["date"].date(), probability=prob, risk_score=score,
                risk_category=category(score), model_version=version, primary_indicator=main[1],
                data_completeness=1.0, calculated_at=calculated, run_id=run.row.id,
                calculation_details={
                    "method": "Trained C02 flood model probability x 100 (uncalibrated model risk score)",
                    "decision_threshold": round(float(bundle["threshold"]), 4),
                    "above_model_alert_threshold": prob >= float(bundle["threshold"]),
                    "inputs": {k: round(float(last[k]), 4) for k in (
                        "rain_1d", "rain_3d", "rain_7d", "rain_30d", "rain_7d_anom", "gwettop", "gwetroot", "gwetprof",
                        "gwettop_rel_q99", "gwetroot_rel_q99", "gwetprof_rel_q99", "t2m", "t2m_max", "rh2m")},
                }))
        db.bulk_insert_mappings(DistrictRiskAssessment, rows)
        db.commit()
        valid = sum(1 for r in rows if r["risk_score"] is not None)
        run.done(len(rows), "success" if valid == len(rows) else "partial",
                 None if valid == len(rows) else f"{len(rows) - valid} districts have insufficient data.")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("Model scoring failed.")
        run.done(0, "failed", e)
    return run.row


# Open-Meteo 7-day forecast -----------------------------------------------------------------------
def refresh_weather_forecast(db, trigger="schedule"):
    seed_districts(db)
    run = Run(db, "open_meteo", trigger)
    try:
        dists = list(db.scalars(select(ForecastDistrict).order_by(ForecastDistrict.pcode)))
        retrieved, rows = now_utc(), []
        for i in range(0, len(dists), 50):
            chunk = dists[i:i + 50]
            r = requests.get(OPEN_METEO, timeout=60, params={
                "latitude": ",".join(f"{d.lat:.4f}" for d in chunk), "longitude": ",".join(f"{d.lon:.4f}" for d in chunk),
                "daily": ",".join(OM_DAILY), "current": ",".join(OM_CURRENT), "forecast_days": 7,
                "timezone": "Asia/Karachi"})
            r.raise_for_status()
            data = r.json()
            data = data if isinstance(data, list) else [data]
            for d, loc in zip(chunk, data):
                for var, unit in OM_DAILY.items():
                    for day, val in zip(loc["daily"]["time"], loc["daily"].get(var, [])):
                        rows.append({"district_pcode": d.pcode, "variable": var, "value": val, "unit": unit,
                                     "source": OM_SOURCE, "forecast_date": date.fromisoformat(day),
                                     "generated_at": retrieved, "retrieved_at": retrieved})
                cur = loc.get("current") or {}
                for var, unit in OM_CURRENT.items():
                    if cur.get(var) is not None:
                        rows.append({"district_pcode": d.pcode, "variable": f"current_{var}", "value": cur[var],
                                     "unit": unit, "source": OM_SOURCE, "forecast_date": today_pkt(),
                                     "generated_at": retrieved, "retrieved_at": retrieved})
        keys = {(r["district_pcode"], r["variable"], r["forecast_date"]) for r in rows}
        dates = {k[2] for k in keys}
        db.execute(delete(HazardForecast).where(HazardForecast.source == OM_SOURCE,
                                                HazardForecast.forecast_date.in_(dates)))
        db.bulk_insert_mappings(HazardForecast, rows)
        db.commit()
        run.done(len(rows))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("Open-Meteo refresh failed.")
        run.done(0, "failed", e)
    return run.row


# NASA EONET observed events ----------------------------------------------------------------------
EONET_CATS = {"floods": "flood", "landslides": "landslide", "severeStorms": "storm", "wildfires": "wildfire"}


def refresh_events(db, trigger="schedule"):
    run = Run(db, "eonet", trigger)
    try:
        r = requests.get(EONET, timeout=60, params={"category": ",".join(EONET_CATS), "bbox": PAK_BBOX, "days": 60,
                                                    "status": "all"})
        r.raise_for_status()
        n, retrieved = 0, now_utc()
        for e in r.json().get("events", []):
            for gi, geo in enumerate(e.get("geometry", [])[-1:]):
                coords = geo.get("coordinates")
                if geo.get("type") != "Point" or not coords:
                    continue
                lon, lat = coords[0], coords[1]
                ext = f"{e['id']}"
                if db.scalar(select(HazardEvent.id).where(HazardEvent.source == EONET_SOURCE, HazardEvent.external_id == ext)):
                    continue
                cat = EONET_CATS.get(e["categories"][0]["id"], e["categories"][0]["id"])
                when = geo.get("date")
                db.add(HazardEvent(external_id=ext, source=EONET_SOURCE, category=cat, title=e["title"][:300],
                                   event_date=datetime.fromisoformat(when.replace("Z", "+00:00")).replace(tzinfo=None)
                                   if when else None, lat=lat, lon=lon, link=(e.get("link") or "")[:500] or None,
                                   district_pcode=district_at(lat, lon), retrieved_at=retrieved))
                n += 1
        db.commit()
        run.done(n)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("EONET refresh failed.")
        run.done(0, "failed", e)
    return run.row


# NASA GPM IMERG Early Run daily rainfall (needs EARTHDATA_TOKEN and the GES DISC EULA accepted) -------
IMERG_SOURCE = "NASA GPM IMERG Early Run"
CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"
IMERG_DAYS = 7
# 0.1 degree global grid: lon -179.95..179.95 (3600), lat -89.95..89.95 (1800). Pakistan window:
LON0, LON1, LAT0, LAT1 = 2400, 2585, 1130, 1275


@lru_cache(maxsize=1)
def imerg_district_cells():
    """Grid cells (lon_idx, lat_idx) whose centre lies in each district; nearest cell for tiny districts."""
    out = defaultdict(list)
    shapes = district_shapes()
    for i in range(LON0, LON1):
        lon = -179.95 + 0.1 * i
        for j in range(LAT0, LAT1):
            p = Point(lon, -89.95 + 0.1 * j)
            for pcode, geom in shapes:
                if geom.bounds[0] <= p.x <= geom.bounds[2] and geom.bounds[1] <= p.y <= geom.bounds[3] and geom.contains(p):
                    out[pcode].append((i - LON0, j - LAT0))
                    break
    for pcode, geom in shapes:
        if not out[pcode]:
            rp = geom.representative_point()
            out[pcode].append((round((rp.x + 179.95) / 0.1) - LON0, round((rp.y + 89.95) / 0.1) - LAT0))
    return dict(out)


def refresh_imerg(db, trigger="schedule"):
    import io

    import h5py

    run = Run(db, "imerg", trigger)
    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        run.done(0, "failed", "EARTHDATA_TOKEN is not configured.")
        return run.row
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    rows, errors, retrieved = [], [], now_utc()
    cellmap = imerg_district_cells()
    # Ask NASA CMR for the latest daily granules: the file version (V07B, V07C ...) changes over time.
    try:
        cmr = requests.get(CMR_GRANULES, timeout=60, params={"short_name": "GPM_3IMERGDE", "sort_key": "-start_date",
                                                             "page_size": IMERG_DAYS}).json()["feed"]["entry"]
        granules = [(date.fromisoformat(e["time_start"][:10]),
                     next(lk["href"] for lk in e["links"] if lk["href"].startswith("https://data.gesdisc")
                          and lk["href"].endswith(".nc4"))) for e in cmr]
    except Exception as e:  # noqa: BLE001
        run.done(0, "failed", f"NASA CMR granule search failed: {e}")
        return run.row
    for d, url in granules:
        try:
            r = s.get(url, timeout=180)
            if r.status_code == 403 and "EULA" in r.text:
                run.done(0, "failed", "NASA GES DISC EULA not accepted for this Earthdata account. Approve it at "
                                      "https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ")
                return run.row
            if r.status_code == 404:
                errors.append(f"{d}: not yet published")
                continue
            r.raise_for_status()
            with h5py.File(io.BytesIO(r.content), "r") as f:
                grid = f["precipitation"][0, LON0:LON1, LAT0:LAT1]  # (lon, lat), mm/day
            grid = np.where(grid < 0, np.nan, grid)
            for pcode, idx in cellmap.items():
                vals = [grid[i, j] for i, j in idx]
                v = float(np.nanmean(vals)) if not np.all(np.isnan(vals)) else None
                if v is not None:
                    rows.append({"district_pcode": pcode, "variable": "precipitation", "value": v, "unit": "mm/day",
                                 "source": IMERG_SOURCE, "observation_date": d, "retrieved_at": retrieved})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d}: {e}")
    try:
        if rows:
            dates = {r["observation_date"] for r in rows}
            db.execute(delete(HazardObservation).where(HazardObservation.source == IMERG_SOURCE,
                                                       HazardObservation.observation_date.in_(dates)))
            db.bulk_insert_mappings(HazardObservation, rows)
            db.commit()
        run.done(len(rows), "success" if rows and not errors else ("partial" if rows else "failed"),
                 "; ".join(errors)[:1000] or None)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run.done(0, "failed", e)
    return run.row


def refresh_all(db, trigger="schedule"):
    """The controlled refresh: observations -> model scores -> weather forecast -> events -> IMERG."""
    out = [refresh_observations(db, trigger)]
    out.append(score_districts(db, trigger))
    out.append(refresh_weather_forecast(db, trigger))
    out.append(refresh_events(db, trigger))
    out.append(refresh_imerg(db, trigger))
    return out

