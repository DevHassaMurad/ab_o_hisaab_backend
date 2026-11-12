"""PDMA risk forecasting API (PDMA staff only). Reads stored data; the refresh endpoint is rate-limited."""
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import SessionLocal, get_db
from controller.authentication import AuthFlowError
from controller.forecast_queries import ForecastQueries
from controller.forecasting import refresh_all
from middlewares.role_middleware import PDMA_STAFF, require_roles
from model.forecast_model import ForecastRun

router = APIRouter(prefix="/forecast", tags=["Risk forecasting"])
staff_only = require_roles(*PDMA_STAFF)
GEO = Path(__file__).resolve().parent.parent / "data" / "geo"
Category = Literal["low", "watch", "warning", "immediate", "insufficient"]
LayerKey = Literal["risk", "rain_observed", "rain_forecast", "flood_observed", "landslide", "heat"]
REFRESH_COOLDOWN = timedelta(minutes=15)
_refresh_lock = Lock()


@router.get("/summary")
def summary(province: str | None = Query(default=None, max_length=100), _=Depends(staff_only),
            db: Session = Depends(get_db)):
    return ForecastQueries(db).summary(province)


@router.get("/districts")
def districts(province: str | None = Query(default=None, max_length=100),
              district: str | None = Query(default=None, max_length=12), category: Category | None = None,
              hazard: Literal["flood", "heat"] | None = None, page: int = Query(default=1, ge=1),
              page_size: int = Query(default=50, ge=1, le=200), _=Depends(staff_only), db: Session = Depends(get_db)):
    return ForecastQueries(db).table(province, district, category, hazard, page, page_size)


@router.get("/districts/{pcode}")
def district_detail(pcode: str, _=Depends(staff_only), db: Session = Depends(get_db)):
    return ForecastQueries(db).detail(pcode[:12])


@router.get("/layers")
def layers(_=Depends(staff_only), db: Session = Depends(get_db)):
    return ForecastQueries(db).layers()


@router.get("/layers/{key}")
def layer_data(key: LayerKey, _=Depends(staff_only), db: Session = Depends(get_db)):
    return ForecastQueries(db).layer_data(key)


@router.get("/geo/{name}")
def geo(name: Literal["districts", "provinces"], _=Depends(staff_only)):
    data = (GEO / f"pak_{name}.geojson").read_bytes()
    return Response(data, media_type="application/geo+json", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/model-performance")
def model_performance(_=Depends(staff_only)):
    return ForecastQueries.model_performance()


@router.get("/runs")
def runs(limit: int = Query(default=20, ge=1, le=100), _=Depends(staff_only), db: Session = Depends(get_db)):
    return ForecastQueries(db).runs(limit)


def _refresh_job():
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            refresh_all(db, "manual")
    finally:
        _refresh_lock.release()


@router.post("/refresh", status_code=202)
def refresh(background_tasks: BackgroundTasks, _=Depends(staff_only), db: Session = Depends(get_db)):
    if _refresh_lock.locked():
        raise AuthFlowError("REFRESH_RUNNING", "A data refresh is already running.", 409)
    last = db.scalar(select(ForecastRun).where(ForecastRun.kind == "nasa_power", ForecastRun.trigger == "manual")
                     .order_by(ForecastRun.id.desc()).limit(1))
    if last and datetime.utcnow() - last.started_at < REFRESH_COOLDOWN:
        raise AuthFlowError("REFRESH_TOO_SOON", "Data was refreshed recently. Try again in a few minutes.", 429)
    background_tasks.add_task(_refresh_job)
    return {"status": "started"}


# Hackathon challenges C02 / C06 / C12 (ml_engine) ------------------------------------------------------------
@router.get("/challenges")
def challenges(_=Depends(staff_only)):
    from controller.ml_challenges import challenges_overview

    return challenges_overview()


@router.get("/challenges/images/{key}")
def challenge_image(key: Literal["c02-dashboard", "c02-final", "c02-pr-curve", "c06-shap", "c12-matrix"],
                    _=Depends(staff_only)):
    from controller.ml_challenges import image_path

    return Response(image_path(key).read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/districts/{pcode}/explain")
def district_explain(pcode: str, _=Depends(staff_only), db: Session = Depends(get_db)):
    from controller.ml_challenges import explain_district

    return explain_district(db, pcode[:12])


@router.get("/districts/{pcode}/verification")
def district_verification(pcode: str, _=Depends(staff_only), db: Session = Depends(get_db)):
    from controller.ml_challenges import verify_district

    return verify_district(db, pcode[:12])
