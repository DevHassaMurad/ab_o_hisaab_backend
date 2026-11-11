"""Earth Engine seam (FR2). All `ee` calls live here, behind EarthEngineGateway, so services and tests never need
credentials. `ee` is imported lazily; install it with `pip install -e ".[ee]"`.

Dataset IDs and band names come from settings; check them in the Earth Engine catalog before relying on them."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.config import Settings, settings


class EEUnavailable(Exception):
    """Earth Engine is not configured, unreachable or returned a non-retryable error."""


@dataclass
class TehsilFeature:
    pcode: str
    geometry: dict[str, Any] | None  # GeoJSON; None when boundaries are not loaded yet
    lat: float
    lon: float


@dataclass
class TehsilRain:
    pcode: str
    rainfall_mm: float | None
    rainfall_max_3h_mm: float | None
    flags: list[str] = field(default_factory=list)


@dataclass
class TehsilFlood:
    pcode: str
    flooded_area_km2: float


@dataclass(frozen=True)
class S1Params:
    diff_db: float = -3.0
    after_max_db: float = -15.0
    speckle_radius_m: int = 50
    perm_occurrence: int = 80
    max_slope_deg: float = 5.0
    min_connected_px: int = 8
    area_scale_m: int = 30
    orbit_pass: str | None = None

    @classmethod
    def from_settings(cls, s: Settings = settings) -> S1Params:
        return cls(s.S1_DIFF_DB, s.S1_AFTER_MAX_DB, s.S1_SPECKLE_RADIUS_M, s.JRC_PERM_OCCURRENCE, s.S1_MAX_SLOPE_DEG,
                   s.S1_MIN_CONNECTED_PX, s.S1_AREA_SCALE_M)


@dataclass
class S1Change:
    image: Any
    before_dates: list[date]
    after_dates: list[date]
    status: str  # OK | NO_IMAGERY


class EarthEngineGateway(Protocol):
    def image_count(self, day: date) -> int: ...
    def reduce_imerg(self, day: date, features: list[TehsilFeature]) -> list[TehsilRain]: ...
    def compute_terrain(self, features: list[TehsilFeature]) -> list[dict[str, Any]]: ...


_RETRYABLE = ("Too many concurrent", "Computation timed out", "Internal error", "429", "503")


class EEGateway:
    """Real implementation. Every call goes through _call() for retries."""

    _initialised = False

    def __init__(self, s: Settings = settings, sleep=time.sleep) -> None:
        self.s = s
        self.sleep = sleep

    # ---- setup ----
    def init(self) -> Any:
        try:
            import ee
        except ImportError as exc:
            raise EEUnavailable('earthengine-api is not installed (pip install -e ".[ee]")') from exc
        if not EEGateway._initialised:
            if not (self.s.GEE_SERVICE_ACCOUNT and self.s.GEE_KEY_FILE and Path(self.s.GEE_KEY_FILE).exists()):
                raise EEUnavailable("Earth Engine not configured (GEE_SERVICE_ACCOUNT / GEE_KEY_FILE)")
            creds = ee.ServiceAccountCredentials(self.s.GEE_SERVICE_ACCOUNT, self.s.GEE_KEY_FILE)
            url = "https://earthengine-highvolume.googleapis.com" if self.s.GEE_HIGH_VOLUME else None
            ee.Initialize(credentials=creds, project=self.s.GEE_PROJECT or None, opt_url=url)
            EEGateway._initialised = True
        return ee

    def _call(self, fn, *a, **kw):
        ee = self.init()
        for attempt in range(4):
            try:
                return fn(*a, **kw)
            except ee.EEException as exc:
                if attempt == 3 or not any(t in str(exc) for t in _RETRYABLE):
                    raise EEUnavailable(str(exc)) from exc
                self.sleep(5 * 2 ** attempt)
        raise EEUnavailable("unreachable")  # pragma: no cover

    def _fc(self, features: list[TehsilFeature]):
        ee = self.init()
        feats = []
        for f in features:
            geom = ee.Geometry(f.geometry) if f.geometry else ee.Geometry.Point([f.lon, f.lat])
            feats.append(ee.Feature(geom, {"pcode": f.pcode}))
        return ee.FeatureCollection(feats)

    # ---- IMERG ----
    def _imerg_day(self, day: date):
        ee = self.init()
        start = ee.Date(day.isoformat())
        return (ee.ImageCollection(self.s.IMERG_COLLECTION).filterDate(start, start.advance(1, "day"))
                .select(self.s.IMERG_BAND).sort("system:time_start"))

    def image_count(self, day: date) -> int:
        return int(self._call(self._imerg_day(day).size().getInfo))

    def imerg_daily_image(self, day: date):
        ee = self.init()
        col = self._imerg_day(day)
        n = col.size()
        lst = col.toList(48)
        daily = col.sum().multiply(0.5).rename("daily_mm")  # mm/hr x 0.5 h per slot

        def window_sum(i):
            i = ee.Number(i)
            return ee.ImageCollection.fromImages(lst.slice(i, i.add(6))).sum().multiply(0.5)

        windows = ee.List.sequence(0, n.subtract(6)).map(window_sum)
        max3h = ee.ImageCollection.fromImages(windows).max().rename("max3h_mm")
        return daily.addBands(max3h)

    def reduce_imerg(self, day: date, features: list[TehsilFeature]) -> list[TehsilRain]:
        ee = self.init()
        img = self.imerg_daily_image(day)
        rows: list[TehsilRain] = []
        by_pcode = {f.pcode: f for f in features}
        for i in range(0, len(features), self.s.EE_TEHSIL_BATCH):
            batch = features[i:i + self.s.EE_TEHSIL_BATCH]
            out = img.reduceRegions(collection=self._fc(batch), reducer=ee.Reducer.mean(),
                                    scale=self.s.IMERG_REDUCE_SCALE_M, tileScale=2)
            missing = []
            for f in self._call(out.getInfo)["features"]:
                p = f["properties"]
                if p.get("daily_mm") is None:
                    missing.append(p["pcode"])
                    continue
                flags = [] if by_pcode[p["pcode"]].geometry else ["no_boundary_point"]
                rows.append(TehsilRain(p["pcode"], round(p["daily_mm"], 2), _r(p.get("max3h_mm")), flags))
            if missing:  # tiny tehsils: sample a point inside the polygon
                pts = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([by_pcode[c].lon, by_pcode[c].lat]),
                                                       {"pcode": c}) for c in missing])
                samp = img.reduceRegions(collection=pts, reducer=ee.Reducer.first(), scale=11132)
                for f in self._call(samp.getInfo)["features"]:
                    p = f["properties"]
                    rows.append(TehsilRain(p["pcode"], _r(p.get("daily_mm")), _r(p.get("max3h_mm")),
                                           ["centroid_fallback"]))
        return rows

    # ---- terrain (one-off) ----
    def compute_terrain(self, features: list[TehsilFeature]) -> list[dict[str, Any]]:
        ee = self.init()
        dem = ee.Image(self.s.SRTM_COLLECTION).select("elevation")
        img = dem.rename("elev").addBands(ee.Terrain.slope(dem).rename("slope"))
        out = img.reduceRegions(collection=self._fc(features), reducer=ee.Reducer.mean(), scale=90, tileScale=4)
        return [{"tehsil_pcode": f["properties"]["pcode"], "elevation_mean_m": _r(f["properties"].get("elev"), 1),
                 "slope_mean_deg": _r(f["properties"].get("slope"), 2)}
                for f in self._call(out.getInfo)["features"]]

    # ---- Sentinel-1 (primitives used by FR21) ----
    def s1_collection(self, region, start: date, end: date, orbit_pass: str | None = None):
        ee = self.init()
        c = (ee.ImageCollection(self.s.S1_COLLECTION).filterBounds(region)
             .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
             .filter(ee.Filter.eq("instrumentMode", "IW"))
             .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")).select("VV"))
        return c.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass)) if orbit_pass else c

    def s1_acquisitions(self, region, start: date, end: date, orbit_pass: str | None = None) -> list[date]:
        ms = self._call(self.s1_collection(region, start, end, orbit_pass).aggregate_array("system:time_start").getInfo)
        return sorted({datetime.fromtimestamp(m / 1000, tz=UTC).date() for m in ms})

    def s1_flood_change(self, region, before: tuple[date, date], after: tuple[date, date], p: S1Params) -> S1Change:
        ee = self.init()
        b_dates = self.s1_acquisitions(region, *before, p.orbit_pass)
        a_dates = self.s1_acquisitions(region, *after, p.orbit_pass)
        if not b_dates or not a_dates:
            return S1Change(None, b_dates, a_dates, "NO_IMAGERY")

        def smooth(i):
            return i.focal_median(p.speckle_radius_m, "circle", "meters")

        b = smooth(self.s1_collection(region, *before, p.orbit_pass).median())
        a = smooth(self.s1_collection(region, *after, p.orbit_pass).sort("system:time_start").mosaic())
        new_water = a.subtract(b).lt(p.diff_db).And(a.lt(p.after_max_db))  # S1 GRD in EE is already dB
        perm = ee.Image(self.s.JRC_COLLECTION).select("occurrence").gt(p.perm_occurrence).unmask(0)
        slope = ee.Terrain.slope(ee.Image(self.s.SRTM_COLLECTION).select("elevation"))
        flooded = new_water.And(perm.Not()).And(slope.lt(p.max_slope_deg))
        flooded = flooded.updateMask(flooded.connectedPixelCount(100, True).gte(p.min_connected_px))
        img = (b.rename("before_vv").addBands(a.rename("after_vv"))
               .addBands(flooded.rename("flooded").unmask(0)).clip(region))
        return S1Change(img, b_dates, a_dates, "OK")

    def flood_area_by_tehsil(self, ch: S1Change, features: list[TehsilFeature], p: S1Params) -> list[TehsilFlood]:
        ee = self.init()
        area = ch.image.select("flooded").multiply(ee.Image.pixelArea()).rename("flooded_m2")
        out = area.reduceRegions(collection=self._fc(features), reducer=ee.Reducer.sum(), scale=p.area_scale_m,
                                 tileScale=8)
        return [TehsilFlood(f["properties"]["pcode"], round((f["properties"].get("sum") or 0) / 1e6, 3))
                for f in self._call(out.getInfo)["features"]]

    def thumbnail_png(self, image, bbox: tuple[float, float, float, float], vis: dict[str, Any],
                      dimensions: int | None = None) -> bytes:
        ee = self.init()
        region = ee.Geometry.Rectangle(list(bbox), proj="EPSG:4326", geodesic=False)
        url = self._call(image.getThumbURL, {**vis, "region": region, "dimensions": dimensions or
                                             self.s.THUMB_DIMENSIONS, "format": "png", "crs": "EPSG:3857"})
        r = httpx.get(url, timeout=120)  # URLs expire: download immediately
        r.raise_for_status()
        return r.content

    def health(self) -> dict[str, Any]:
        """Smoke check: auth plus the latest IMERG image time."""
        ee = self.init()
        latest = self._call(ee.ImageCollection(self.s.IMERG_COLLECTION).sort("system:time_start", False)
                            .first().get("system:time_start").getInfo)
        return {"ok": True, "latest_imerg_utc": datetime.fromtimestamp(latest / 1000, tz=UTC).isoformat()}


VIS_SAR_BEFORE = {"bands": ["before_vv"], "min": -25, "max": 0}
VIS_SAR_AFTER = {"bands": ["after_vv"], "min": -25, "max": 0}


def _r(v: Any, nd: int = 2) -> float | None:
    return None if v is None else round(float(v), nd)
