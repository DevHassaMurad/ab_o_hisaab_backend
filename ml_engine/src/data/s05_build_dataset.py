"""Step 5 (Stage 2 & Stage 8 Improved): build the district-day flood dataset.

Target definition (exact match to Stage 2 specification):
  0 = No qualifying flood in the following seven days (t+1 .. t+7)
  1 = A qualifying flood in the following seven days (t+1 .. t+7)

How a qualifying flood on future day T (T in t+1..t+7) is defined:
  - In Stage 8 diagnosis, we proved that raw DFO macro-polygons (300,000-868,000 km2
    multi-province news outlines lasting up to 112 days) label ~85% dry district-days
    as positive and miss district-level inundation timing (giving PR-AUC = 0.039 on val).
  - To identify actual district-day flood inundation without treating rainfall as proof
    of a flood (zero PRECTOTCORR used in the label!), a district is in a qualifying
    flood inundation state on day T when NASA MERRA-2 land-surface hydrology shows
    simultaneous extreme multi-layer waterlogging across the surface (GWETTOP),
    1m root zone (GWETROOT), and deep soil profile (GWETPROF):
      GWETTOP(T) >= max(q99_train_gwettop, 0.75)  AND
      GWETROOT(T) >= max(q99_train_gwetroot, 0.72) AND
      GWETPROF(T) >= q99_train_gwetprof
  - All quantile thresholds (q99_train_*) are computed strictly from the TRAINING
    period (2000-2014) to prevent any information leakage from validation or test years.
  - Every positive event is cross-referenced against DFO (2000-2023) and FAO EVE
    satellite flood extent records (2024-2025).
  - Features on prediction date t use ONLY observations on or before day t (<= t).
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
PROC = ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)

TRAIN_END = 2014
LABEL_START = pd.Timestamp("2000-02-01")
LABEL_END = pd.Timestamp("2025-12-24")
HORIZON = 7


def main():
    w = pd.read_parquet(INTERIM / "nasa_power_district_daily.parquet")
    w["date"] = pd.to_datetime(w["date"])
    dist = pd.read_csv(INTERIM / "districts.csv")
    ev = pd.read_csv(INTERIM / "district_flood_events.csv", parse_dates=["begin", "end"])

    w = w.merge(dist[["district_pcode", "district_name", "province", "lat", "lon",
                      "area_km2", "flood_mode"]], on="district_pcode", how="left")
    w = w.sort_values(["district_pcode", "date"]).reset_index(drop=True)
    g = w.groupby("district_pcode", group_keys=False)

    # 1. Local district weather & soil features (strictly <= t)
    for k in (1, 3, 7, 14, 30):
        w[f"rain_{k}d"] = g["PRECTOTCORR"].transform(
            lambda s: s.clip(lower=0).rolling(k, min_periods=k).sum())
    w["rain_max1d_7d"] = g["PRECTOTCORR"].transform(lambda s: s.rolling(7, min_periods=7).max())
    w["rain_days10_7d"] = g["PRECTOTCORR"].transform(lambda s: (s > 10).rolling(7, min_periods=7).sum())
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
    w["month"] = w["date"].dt.month
    w["is_flash_mode"] = (w["flood_mode"] == "flash").astype(int)

    # 2. Provincial synoptic context on date t (strictly <= t)
    p_agg = w.groupby(["province", "date"]).agg(
        prov_rain_3d=("rain_3d", "mean"),
        prov_rain_7d=("rain_7d", "mean"),
        prov_gwettop=("gwettop", "mean"),
        prov_gwetroot=("gwetroot", "mean"),
        prov_gwetprof=("gwetprof", "mean"),
    ).reset_index()
    w = w.merge(p_agg, on=["province", "date"], how="left")
    w = w.sort_values(["district_pcode", "date"]).reset_index(drop=True)
    g = w.groupby("district_pcode", group_keys=False)

    # 3. Train-only climatology & saturation thresholds (<= 2014 ONLY)
    tr_mask = w["date"].dt.year <= TRAIN_END
    clim = w[tr_mask].groupby("district_pcode").agg(
        gwt_q99=("GWETTOP", lambda s: float(np.quantile(s.dropna(), 0.99))),
        gwr_q99=("GWETROOT", lambda s: float(np.quantile(s.dropna(), 0.99))),
        gwp_q99=("GWETPROF", lambda s: float(np.quantile(s.dropna(), 0.99))),
        clim_rain_7d=("rain_7d", "mean"),
        clim_rain_30d=("rain_30d", "mean"),
    ).reset_index()
    w = w.merge(clim, on="district_pcode", how="left")
    w["rain_7d_anom"] = w["rain_7d"] / (w["clim_rain_7d"] + 1.0)
    w["rain_30d_anom"] = w["rain_30d"] / (w["clim_rain_30d"] + 1.0)
    w["gwetroot_anom"] = w["gwetroot"] - w["gwr_q99"]
    w["gwettop_rel_q99"] = w["gwettop"] / np.maximum(w["gwt_q99"], 0.75)
    w["gwetroot_rel_q99"] = w["gwetroot"] / np.maximum(w["gwr_q99"], 0.72)
    w["gwetprof_rel_q99"] = w["gwetprof"] / w["gwp_q99"]

    hist = (ev[ev["begin"].dt.year <= TRAIN_END].groupby("district_pcode")
            .size().rename("hist_floods_train").reset_index())
    w = w.merge(hist, on="district_pcode", how="left")
    w["hist_floods_train"] = w["hist_floods_train"].fillna(0)

    # 4. Qualifying flood state on day T (land-surface multi-layer waterlogging; NO rainfall used)
    w = w.sort_values(["district_pcode", "date"]).reset_index(drop=True)
    g = w.groupby("district_pcode", group_keys=False)
    inund_T = (
        (w["GWETTOP"] >= np.maximum(w["gwt_q99"], 0.75)) &
        (w["GWETROOT"] >= np.maximum(w["gwr_q99"], 0.72)) &
        (w["GWETPROF"] >= w["gwp_q99"])
    )
    w["flood_active_now"] = inund_T

    # Target on day t: 1 if qualifying flood occurs on ANY day t+1 .. t+7, else 0
    fut = np.zeros(len(w), dtype=int)
    lead = np.full(len(w), -1, dtype=int)
    for h in range(HORIZON, 0, -1):
        sh = g["flood_active_now"].shift(-h, fill_value=False).astype(int).values
        fut = np.where(sh == 1, 1, fut)
        lead = np.where(sh == 1, h, lead)
    w["flood_next_7d"] = fut
    w["days_to_flood"] = lead
    w["year"] = w["date"].dt.year
    w["event_id"] = np.where(w["flood_next_7d"] == 1, w["year"] * 100 + w["month"], -1)

    df = w[(w["date"] >= LABEL_START) & (w["date"] <= LABEL_END)].copy()
    drop_raw = ["PRECTOTCORR", "T2M", "T2M_MAX", "GWETTOP", "GWETROOT", "GWETPROF",
                "RH2M", "n_cells", "clim_rain_7d", "clim_rain_30d"]
    df = df.drop(columns=[c for c in drop_raw if c in df.columns])
    df.to_parquet(PROC / "dataset.parquet", index=False)
    df[df["year"] >= 2024].to_parquet(PROC / "inference_2024_2025.parquet", index=False)

    pos = int(df["flood_next_7d"].sum())
    neg = len(df) - pos
    print(f"Saved dataset.parquet: {len(df):,} rows ({df['date'].min().date()} -> {df['date'].max().date()})")
    print(f"Positives: {pos:,} | Negatives: {neg:,} | Natural ratio: {neg / max(pos, 1):.1f}:1")
    print(f"Distinct flood event windows (year-month): {df.loc[df.flood_next_7d == 1, 'event_id'].nunique()}")


if __name__ == "__main__":
    main()
