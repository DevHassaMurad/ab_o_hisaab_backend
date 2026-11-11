"""Tehsil boundaries from PostGIS as simplified GeoJSON features for Earth Engine (FR2)."""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.earth_engine import TehsilFeature
from app.models.geography import Tehsil

SQL = text("""
  SELECT t.pcode,
         CASE WHEN t.geom IS NULL THEN NULL
              ELSE ST_AsGeoJSON(ST_SimplifyPreserveTopology(t.geom, :tol)) END AS g,
         COALESCE(ST_Y(ST_PointOnSurface(t.geom)), d.centroid_lat) AS lat,
         COALESCE(ST_X(ST_PointOnSurface(t.geom)), d.centroid_lon) AS lon
  FROM tehsil t JOIN district d ON d.id = t.district_id
  WHERE t.id = ANY(:ids)
  ORDER BY t.pcode
""")


def tehsil_features(db: Session, tehsils: list[Tehsil]) -> list[TehsilFeature]:
    """ST_PointOnSurface (not centroid) keeps the fallback point inside odd shapes. Tehsils without a boundary
    fall back to their district centroid; reduce_imerg flags those rows `no_boundary_point`."""
    if not tehsils:
        return []
    rows = db.execute(SQL, {"tol": settings.EE_SIMPLIFY_DEG, "ids": [t.id for t in tehsils]}).all()
    return [TehsilFeature(r.pcode, json.loads(r.g) if r.g else None, float(r.lat), float(r.lon)) for r in rows]
