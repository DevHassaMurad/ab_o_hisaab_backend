"""Step 3: fetch daily Copernicus GloFAS river discharge (2000-2025) for all 160 districts.

Source: Open-Meteo Flood API (Copernicus Emergency Management Service GloFAS v4,
0.05 deg resolution, license CC-BY 4.0, no API key required).

Because a district's geometric centroid can fall a few km off the main river
channel on the 0.05 deg grid, we first probe 5 points inside each district
polygon on two major historical flood dates (2010-08-08 and 2022-08-20), select
the highest-flow cell inside each district, and then fetch the full 2000-2025
daily river_discharge time series for all 160 districts.

Output: data/interim/glofas_district_daily.parquet
"""
from pathlib import Path
import time
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
RAW = ROOT / "data" / "raw" / "glofas"
RAW.mkdir(parents=True, exist_ok=True)
API = "https://flood-api.open-meteo.com/v1/flood"


def candidate_points(poly) -> list[tuple[float, float]]:
    """Return 5 points guaranteed to lie inside/near the district polygon."""
    rp = poly.representative_point()
    minx, miny, maxx, maxy = poly.bounds
    pts = [(round(rp.y, 3), round(rp.x, 3))]
    for fy in (0.3, 0.5, 0.7):
        for fx in (0.3, 0.5, 0.7):
            if (fy, fx) == (0.5, 0.5):
                continue
            p = Point(minx + fx * (maxx - minx), miny + fy * (maxy - miny))
            if not poly.contains(p):
                p = poly.representative_point()
            pts.append((round(p.y, 3), round(p.x, 3)))
    # Keep 5 spread-out points (center + 4 corners inside polygon)
    return [pts[0], pts[1], pts[3], pts[6], pts[8]]


def get_with_retry(params, retries=5):
    for i in range(retries):
        try:
            r = requests.get(API, params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (i + 1))
        except requests.RequestException:
            time.sleep(2 * (i + 1))
    raise RuntimeError("GloFAS API call failed after retries")


def main():
    gdf = gpd.read_file(INTERIM / "districts.geojson").to_crs("EPSG:4326")
    cands = []
    for _, row in gdf.iterrows():
        for idx, (la, lo) in enumerate(candidate_points(row.geometry)):
            cands.append({"district_pcode": row["district_pcode"], "idx": idx, "lat": la, "lon": lo})
    cdf = pd.DataFrame(cands)

    # Probe on 2022-08-15..2022-08-25 in batches of 80 points
    peaks = []
    for i in range(0, len(cdf), 80):
        batch = cdf.iloc[i:i + 80]
        js = get_with_retry({
            "latitude": ",".join(batch["lat"].astype(str)),
            "longitude": ",".join(batch["lon"].astype(str)),
            "daily": "river_discharge",
            "start_date": "2022-08-15",
            "end_date": "2022-08-25",
        })
        if isinstance(js, dict):
            js = [js]
        for item in js:
            vals = [v for v in item["daily"]["river_discharge"] if v is not None]
            peaks.append(max(vals) if vals else 0.0)
        time.sleep(0.4)
    cdf["peak_2022"] = peaks
    best = cdf.sort_values(["district_pcode", "peak_2022"], ascending=[True, False]) \
              .groupby("district_pcode", as_index=False).first()
    best.to_csv(INTERIM / "glofas_points.csv", index=False)
    print(f"Selected river cells for {len(best)} districts; median 2022 peak = {best['peak_2022'].median():.1f} m3/s")

    # Fetch 2000-01-01 .. 2025-12-31 in batches of 20 districts
    frames = []
    for i in range(0, len(best), 20):
        batch = best.iloc[i:i + 20]
        js = get_with_retry({
            "latitude": ",".join(batch["lat"].astype(str)),
            "longitude": ",".join(batch["lon"].astype(str)),
            "daily": "river_discharge",
            "start_date": "2000-01-01",
            "end_date": "2025-12-31",
        })
        if isinstance(js, dict):
            js = [js]
        for pcode, item in zip(batch["district_pcode"], js):
            frames.append(pd.DataFrame({
                "district_pcode": pcode,
                "date": pd.to_datetime(item["daily"]["time"]),
                "river_discharge": pd.Series(item["daily"]["river_discharge"], dtype="float64"),
            }))
        print(f"  Fetched {min(i + 20, len(best))}/{len(best)} districts", flush=True)
        time.sleep(0.5)

    out = pd.concat(frames, ignore_index=True)
    out["river_discharge"] = out["river_discharge"].ffill().bfill().fillna(0.0)
    out.to_parquet(INTERIM / "glofas_district_daily.parquet", index=False)
    print(f"Saved {len(out):,} rows to data/interim/glofas_district_daily.parquet")


if __name__ == "__main__":
    main()
