"""FR2: check Earth Engine credentials and dataset access.

    python -m scripts.ee_smoke_test
"""
from datetime import date

from app.integrations.earth_engine import EEGateway, EEUnavailable

if __name__ == "__main__":
    gw = EEGateway()
    try:
        print("health:", gw.health())
        print("IMERG images on 2022-08-26:", gw.image_count(date(2022, 8, 26)))
        ee = gw.init()
        dadu = ee.Geometry.Point([67.78, 26.73]).buffer(30000)
        print("Sentinel-1 acquisitions over Dadu, Aug 2022:", gw.s1_acquisitions(dadu, date(2022, 8, 1),
                                                                                 date(2022, 8, 31)))
    except EEUnavailable as exc:
        raise SystemExit(f"Earth Engine unavailable: {exc}") from exc
