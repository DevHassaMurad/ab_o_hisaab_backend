"""Step 2: download NASA POWER daily weather for every MERRA-2 grid cell that
covers a Pakistan district, then average cells into district-level daily series.

Why grid cells instead of one centroid?  NASA POWER meteorology is on a
0.5 deg (lat) x 0.625 deg (lon) MERRA-2 grid.  Big districts (e.g. Balochistan)
span many cells, so a single centroid would misrepresent them.

These are HISTORICAL OBSERVATIONS / reanalysis, NOT forecasts.

Input : data/interim/districts.geojson
Output: data/raw/nasa_power/cell_<lat>_<lon>.csv   (one per grid cell, cached)
        data/interim/district_cells.csv
        data/interim/nasa_power_district_daily.parquet
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import time

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
RAW = ROOT / "data" / "raw" / "nasa_power"
RAW.mkdir(parents=True, exist_ok=True)

START, END = "20000101", "20251231"
PARAMS = ["PRECTOTCORR", "T2M", "T2M_MAX", "GWETTOP", "GWETROOT", "GWETPROF", "RH2M"]
API = "https://power.larc.nasa.gov/api/temporal/daily/point"
LAT_STEP, LON_STEP = 0.5, 0.625
WORKERS = 4


def build_cells(districts: gpd.GeoDataFrame) -> pd.DataFrame:
    minx, miny, maxx, maxy = districts.total_bounds
    lats = np.arange(np.floor(miny / LAT_STEP) * LAT_STEP, maxy + LAT_STEP, LAT_STEP)
    lons = np.arange(np.floor(minx / LON_STEP) * LON_STEP, maxx + LON_STEP, LON_STEP)
    pts = gpd.GeoDataFrame(
        [{"lat": round(la, 3), "lon": round(lo, 3)} for la in lats for lo in lons],
        geometry=[Point(lo, la) for la in lats for lo in lons], crs="EPSG:4326")
    joined = gpd.sjoin(pts, districts[["district_pcode", "geometry"]], predicate="within")
    rows = joined[["district_pcode", "lat", "lon"]].copy()

    # Small districts with no cell centre inside -> use nearest cell centre
    missing = set(districts["district_pcode"]) - set(rows["district_pcode"])
    extra = []
    for _, d in districts[districts["district_pcode"].isin(missing)].iterrows():
        rp = d.geometry.representative_point()
        la = round(round(rp.y / LAT_STEP) * LAT_STEP, 3)
        lo = round(round(rp.x / LON_STEP) * LON_STEP, 3)
        extra.append({"district_pcode": d["district_pcode"], "lat": la, "lon": lo})
    rows = pd.concat([rows, pd.DataFrame(extra)], ignore_index=True)
    print(f"Districts with own cells: {len(districts) - len(missing)}, "
          f"using nearest cell: {len(missing)}")
    return rows


def cell_path(lat, lon) -> Path:
    return RAW / f"cell_{lat:.3f}_{lon:.3f}.csv"


def fetch_cell(lat, lon, retries=4) -> str:
    out = cell_path(lat, lon)
    if out.exists() and out.stat().st_size > 1000:
        return "cached"
    params = {"parameters": ",".join(PARAMS), "community": "AG",
              "latitude": lat, "longitude": lon, "start": START, "end": END,
              "format": "CSV", "time-standard": "UTC"}
    for i in range(retries):
        try:
            r = requests.get(API, params=params, timeout=180)
            if r.status_code == 200 and "-END HEADER-" in r.text:
                body = r.text.split("-END HEADER-", 1)[1].strip()
                out.write_text(body)
                return "ok"
            time.sleep(5 * (i + 1))
        except requests.RequestException:
            time.sleep(5 * (i + 1))
    return "FAILED"


def load_cell(lat, lon) -> pd.DataFrame:
    df = pd.read_csv(cell_path(lat, lon))
    df["date"] = pd.to_datetime(df["YEAR"].astype(str), format="%Y") + \
        pd.to_timedelta(df["DOY"] - 1, unit="D")
    df = df.replace(-999, np.nan).drop(columns=["YEAR", "DOY"])
    df["lat"], df["lon"] = lat, lon
    return df


def main():
    districts = gpd.read_file(INTERIM / "districts.geojson")
    dc = build_cells(districts)
    dc.to_csv(INTERIM / "district_cells.csv", index=False)
    cells = dc[["lat", "lon"]].drop_duplicates().values.tolist()
    print(f"Unique grid cells to download: {len(cells)}")

    status = {"ok": 0, "cached": 0, "FAILED": 0}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch_cell, la, lo): (la, lo) for la, lo in cells}
        for n, f in enumerate(as_completed(futs), 1):
            status[f.result()] += 1
            if n % 25 == 0 or n == len(cells):
                print(f"  {n}/{len(cells)} {status}", flush=True)
    if status["FAILED"]:
        raise SystemExit("Some cells failed - re-run the script (cached cells are skipped).")

    # Aggregate cells -> district daily mean
    allc = pd.concat([load_cell(la, lo) for la, lo in cells], ignore_index=True)
    merged = dc.merge(allc, on=["lat", "lon"])
    daily = merged.groupby(["district_pcode", "date"])[PARAMS].mean().reset_index()
    daily["n_cells"] = merged.groupby(["district_pcode", "date"]).size().values
    daily.to_parquet(INTERIM / "nasa_power_district_daily.parquet", index=False)
    print(f"\nSaved {len(daily):,} district-day rows; "
          f"{daily['district_pcode'].nunique()} districts; "
          f"{daily['date'].min().date()} -> {daily['date'].max().date()}")
    print("Missing values per column:\n", daily[PARAMS].isna().sum().to_string())


if __name__ == "__main__":
    main()
