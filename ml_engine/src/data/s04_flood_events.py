"""Step 4: turn documented flood events into district-level flood records.

Label source (REAL, documented): Dartmouth Flood Observatory "Global Flood
Records" 1985-2023 (Zenodo doi:10.5281/zenodo.19288170).  Each record has a
begin date, end date and a mapped affected-area polygon.

A district counts as flooded by an event if the event polygon covers at least
MIN_OVERLAP of the district's area.  Rainfall is NOT used here - labels come
only from the flood archive.

Output: data/interim/district_flood_events.csv
"""
from pathlib import Path
import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
DFO = ROOT / "data" / "raw" / "floods" / "dfo_Global_Flood_Records.gpkg"
MIN_OVERLAP = 0.20      # share of district area inside event polygon
EQ_AREA = "EPSG:6933"


def main():
    ev = gpd.read_file(DFO)
    ev = ev[ev["Country"].str.contains("Pakistan", na=False)].copy()
    ev["BeginDate"] = pd.to_datetime(ev["BeginDate"])
    ev["EndDate"] = pd.to_datetime(ev["EndDate"])
    ev = ev[ev["BeginDate"].dt.year >= 1999]          # weather starts 2000
    ev["geometry"] = ev.geometry.make_valid()
    print(f"Pakistan DFO events since 1999: {len(ev)}")

    dist = gpd.read_file(INTERIM / "districts.geojson").to_crs(EQ_AREA)
    dist["d_area"] = dist.area
    ev_eq = ev.to_crs(EQ_AREA)

    inter = gpd.overlay(dist[["district_pcode", "d_area", "geometry"]],
                        ev_eq[["ReportNumber", "BeginDate", "EndDate", "MainCause",
                               "Severity", "geometry"]],
                        how="intersection", keep_geom_type=True)
    inter["overlap_frac"] = inter.area / inter["d_area"]
    inter = (inter.groupby(["ReportNumber", "district_pcode"], as_index=False)
             .agg(begin=("BeginDate", "first"), end=("EndDate", "first"),
                  cause=("MainCause", "first"), severity=("Severity", "first"),
                  overlap_frac=("overlap_frac", "sum")))
    rec = inter[inter["overlap_frac"] >= MIN_OVERLAP].copy()
    rec = rec.rename(columns={"ReportNumber": "event_id"})
    rec["overlap_frac"] = rec["overlap_frac"].clip(upper=1).round(3)
    rec["source"] = "DFO Global Flood Records (Zenodo 19288170)"
    rec = rec.sort_values(["begin", "district_pcode"])
    rec.to_csv(INTERIM / "district_flood_events.csv", index=False)

    print(f"District-event records (overlap >= {MIN_OVERLAP:.0%}): {len(rec)}")
    print(f"Events with >=1 district: {rec['event_id'].nunique()}")
    print(f"Districts ever flooded: {rec['district_pcode'].nunique()} / {len(dist)}")
    yr = rec.assign(year=rec["begin"].dt.year).groupby("year").agg(
        events=("event_id", "nunique"), district_records=("district_pcode", "size"))
    print("\nPer year:\n" + yr.to_string())
    print("\nMonth of event start:\n" +
          rec["begin"].dt.month.value_counts().sort_index().to_string())


if __name__ == "__main__":
    main()
