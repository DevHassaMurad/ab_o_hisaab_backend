"""Build simplified district (ADM2) and province (ADM1) GeoJSON for the PDMA risk map from the
OCHA/HDX Pakistan boundary shapefiles shipped with the ML project. Run once: python scripts/build_geo.py"""
import json
from pathlib import Path

import shapefile
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "aab_o_hisaab_ml" / "data" / "raw" / "boundaries"
OUT = ROOT / "data" / "geo"


def rounded(geom):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 4), round(c[1], 4)]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def build(name, fields, out, tolerance):
    reader = shapefile.Reader(str(SRC / f"{name}.shp"), encoding="utf-8")
    feats = []
    for sr in reader.iterShapeRecords():
        rec = sr.record.as_dict()
        geom = shape(sr.shape.__geo_interface__).simplify(tolerance, preserve_topology=True)
        feats.append({"type": "Feature", "properties": {k: rec[v] for k, v in fields.items()},
                      "geometry": rounded(geom)})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / out).write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print(out, len(feats), f"{(OUT / out).stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    build("pak_admin2", {"pcode": "adm2_pcode", "name": "adm2_name", "province": "adm1_name",
                         "province_pcode": "adm1_pcode"}, "pak_districts.geojson", 0.005)
    build("pak_admin1", {"pcode": "adm1_pcode", "name": "adm1_name"}, "pak_provinces.geojson", 0.01)
