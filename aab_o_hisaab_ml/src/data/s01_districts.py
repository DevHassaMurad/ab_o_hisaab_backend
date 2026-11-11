"""Step 1: build the district master table from OCHA COD-AB boundaries.

Input : data/raw/boundaries/pak_admin2.shp  (HDX cod-ab-pak, downloaded)
Output: data/interim/districts.csv, data/interim/districts.geojson
"""
from pathlib import Path
import geopandas as gpd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "boundaries"
OUT = ROOT / "data" / "interim"
OUT.mkdir(parents=True, exist_ok=True)

# Flood mode follows project plan FR4: Sindh = slow onset, KP = flash.
# Mountainous / arid provinces default to flash; reviewable later.
FLASH_KEYS = ("khyber", "gilgit", "kashmir", "jammu", "balochistan")


def find_adm2_file() -> Path:
    files = [p for p in RAW.rglob("*.shp")
             if ("admin2" in p.name.lower() or "adm2" in p.name.lower())
             and not p.stem.lower().endswith("_em")]
    if not files:
        raise FileNotFoundError(f"No admin2 shapefile found under {RAW}")
    print("Using:", files[0].name)
    return files[0]


def pick(cols, *cands):
    low = {c.lower(): c for c in cols}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    raise KeyError(f"None of {cands} found. Columns are: {list(cols)}")


def main():
    gdf = gpd.read_file(find_adm2_file()).to_crs("EPSG:4326")
    print("Columns:", list(gdf.columns))
    c_name = pick(gdf.columns, "ADM2_EN", "adm2_name")
    c_code = pick(gdf.columns, "ADM2_PCODE", "adm2_pcode")
    c_prov = pick(gdf.columns, "ADM1_EN", "adm1_name")

    pts = gdf.representative_point()                 # guaranteed inside polygon
    area_km2 = gdf.to_crs("EPSG:6933").area / 1e6    # equal-area projection

    df = gdf[[c_code, c_name, c_prov]].copy()
    df.columns = ["district_pcode", "district_name", "province"]
    df["lat"] = pts.y.round(4)
    df["lon"] = pts.x.round(4)
    df["area_km2"] = area_km2.round(1)
    df["flood_mode"] = df["province"].str.lower().apply(
        lambda p: "flash" if any(k in p for k in FLASH_KEYS) else "slow")

    assert df["district_pcode"].is_unique, "Duplicate P-codes!"
    assert df[["lat", "lon"]].notna().all().all(), "Missing coordinates!"
    assert df["lat"].between(23, 38).all() and df["lon"].between(60, 78).all(), \
        "Point outside Pakistan bounding box!"

    df = df.sort_values("district_pcode").reset_index(drop=True)
    df.to_csv(OUT / "districts.csv", index=False)
    g = gdf[[c_code, c_name, c_prov, "geometry"]].copy()
    g.columns = ["district_pcode", "district_name", "province", "geometry"]
    g.to_file(OUT / "districts.geojson", driver="GeoJSON")

    print(f"\nDistricts: {len(df)}")
    print(df.groupby("province").size().to_string())
    print("\nFlood modes:\n" + df["flood_mode"].value_counts().to_string())
    print("\nSample:\n", df.head().to_string())


if __name__ == "__main__":
    main()
