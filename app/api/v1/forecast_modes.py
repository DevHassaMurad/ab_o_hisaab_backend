"""FR4 API: list modes, calibration report, officer mode change."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import Conflict, NotFound
from app.core.security import require_role
from app.models.core import User
from app.models.enums import Role
from app.models.geography import District
from app.services.forecasting.model_registry import ModelRegistry
from app.services.forecasting.modes import FloodMode, ModeRegistry, ModeSpec, load_mode_seed, set_district_mode

router = APIRouter(tags=["Forecasting"])


def _mode_out(db: Session, spec: ModeSpec) -> dict[str, Any]:
    info = ModelRegistry().info(spec)
    seed = load_mode_seed()
    validated = set(seed.get("validated_provinces", []))
    provinces = sorted(p for p, m in seed["province_defaults"].items() if m == spec.mode.value)
    count = db.scalar(select(func.count()).select_from(District).where(District.flood_mode == spec.mode.value,
                                                                       District.is_active.is_(True)))
    return {"mode": spec.mode.value, "spec_version": spec.spec_version, "spec_sha256": spec.sha256,
            "description": spec.description, "features": spec.features,
            "lead_weights": {str(k): v for k, v in spec.lead_weights.items()},
            "fallback": spec.fallback.model_dump(), "model_version": info["model_version"],
            "model_loaded": info["loaded"], "district_count": count,
            "validated_provinces": [p for p in provinces if p in validated],
            "unvalidated_provinces": [p for p in provinces if p not in validated]}


def _spec(mode: str) -> ModeSpec:
    try:
        return ModeRegistry.get(mode)
    except (ValueError, KeyError):
        raise NotFound("MODE_NOT_FOUND", f"Unknown mode {mode}.") from None


@router.get("/forecasting/modes")
def list_modes(db: Session = Depends(get_db),
               _: User = Depends(require_role(Role.PDMA_OFFICER, Role.DC, Role.ADMIN))) -> dict:
    items = [_mode_out(db, s) for s in ModeRegistry.all()]
    return {"items": items, "total": len(items), "page": 1, "page_size": 50}


@router.get("/forecasting/modes/{mode}")
def get_mode(mode: str, db: Session = Depends(get_db),
             _: User = Depends(require_role(Role.PDMA_OFFICER, Role.DC, Role.ADMIN))) -> dict:
    return _mode_out(db, _spec(mode))


@router.get("/forecasting/modes/{mode}/calibration")
def calibration(mode: str, _: User = Depends(require_role(Role.PDMA_OFFICER, Role.ADMIN))) -> dict:
    spec = _spec(mode)
    try:
        version = ModelRegistry().version_for(spec)
    except Exception:
        raise NotFound("CALIBRATION_NOT_AVAILABLE", f"No trained {spec.mode} model yet.") from None
    path = Path(settings.FORECAST_ARTIFACT_DIR) / spec.mode_dir / version / "calibration.json"
    if not path.exists():
        raise NotFound("CALIBRATION_NOT_AVAILABLE", "Run ml/threshold_report.py for this model.")
    return {"model_version": version, **json.loads(path.read_text(encoding="utf-8"))}


class ModeChangeIn(BaseModel):
    flood_mode: str
    reason: str = ""


@router.patch("/districts/{district_id}/flood-mode")
def change_mode(district_id: UUID, body: ModeChangeIn, db: Session = Depends(get_db),
                user: User = Depends(require_role(Role.PDMA_OFFICER, Role.ADMIN)),
                x_scenario_id: UUID | None = Header(default=None)) -> dict:
    if x_scenario_id is not None:
        raise Conflict("MODE_LOCKED_IN_REPLAY", "Modes are fixed for a running replay.")
    out = set_district_mode(db, district_id, body.flood_mode, body.reason, actor=user.email)
    out["flood_mode"] = FloodMode(out["flood_mode"]).value
    out["previous_mode"] = FloodMode(out["previous_mode"]).value
    return out
