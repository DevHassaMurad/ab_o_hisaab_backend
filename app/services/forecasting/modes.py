"""FR4: flood mode definitions (YAML validated by ModeSpec), the registry, the effective mode per district (replay
snapshot aware), officer mode changes and the province seed."""
from __future__ import annotations

import hashlib
import itertools
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import get_clock
from app.core.config import settings
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.scenario import current_scenario
from app.models.core import ReplayScenario
from app.models.geography import District
from app.services.stubs import get_audit

CORE_FEATURES = {"rain_3d_mm", "rain_7d_mm", "soil_wetness_root", "elevation_mean_m", "vulnerability_weight"}
TIERS = ("WATCH", "WARNING", "ACTION")


class FloodMode(StrEnum):
    SLOW_ONSET = "SLOW_ONSET"
    FLASH = "FLASH"


class FallbackTerm(BaseModel):
    feature: str
    alt_feature: str | None = None
    weight: float = Field(gt=0, le=1)
    ref: float = Field(gt=0)


class FallbackSpec(BaseModel):
    version: str
    terms: list[FallbackTerm]


class CalibrationSpec(BaseModel):
    method: Literal["isotonic", "sigmoid"] = "isotonic"
    min_positives: int = 50
    false_alarm_budget: dict[Literal["WATCH", "WARNING", "ACTION"], float]


class ModeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: FloodMode
    spec_version: int
    mode_dir: str
    description: str
    artifact_version: str = "current"
    features: list[str]
    monotone: dict[str, Literal[-1, 0, 1]] = Field(default_factory=dict)
    lead_weights: dict[int, float]
    lead_focus: dict[Literal["WATCH", "WARNING", "ACTION"], int]
    label: dict[str, float]
    calibration: CalibrationSpec
    fallback: FallbackSpec
    sha256: str = ""

    @model_validator(mode="after")
    def _check(self) -> ModeSpec:
        from app.services.forecasting.features import FEATURES  # FR3 feature library

        fs = set(self.features)
        if len(fs) != len(self.features):
            raise ValueError("duplicate features")
        if unknown := fs - FEATURES.keys():
            raise ValueError(f"unknown features {sorted(unknown)}")
        if missing := CORE_FEATURES - fs:
            raise ValueError(f"UC04-1 core features missing {sorted(missing)}")
        if "lead_day" not in fs:
            raise ValueError("lead_day required")
        if sorted(self.lead_weights) != list(range(1, 8)):
            raise ValueError("lead_weights must have keys 1..7")
        w = [self.lead_weights[i] for i in range(1, 8)]
        if any(not 0 < x <= 1 for x in w) or any(b > a for a, b in itertools.pairwise(w)):
            raise ValueError("lead_weights must be in (0,1] and non-increasing")
        if abs(sum(t.weight for t in self.fallback.terms) - 1.0) > 1e-6:
            raise ValueError("fallback weights must sum to 1")
        for t in self.fallback.terms:
            if t.feature not in fs or (t.alt_feature and t.alt_feature not in fs):
                raise ValueError(f"fallback feature {t.feature} not in features")
        if set(self.monotone) - fs:
            raise ValueError("monotone keys must be features")
        if set(self.calibration.false_alarm_budget) != set(TIERS):
            raise ValueError("false_alarm_budget needs WATCH, WARNING and ACTION")
        return self


class ModeRegistry:
    _specs: ClassVar[dict[FloodMode, ModeSpec]] = {}

    @classmethod
    def load_all(cls, root: Path | None = None) -> dict[FloodMode, ModeSpec]:
        root = Path(root or settings.FORECAST_MODES_DIR)
        specs: dict[FloodMode, ModeSpec] = {}
        for f in sorted(root.glob("*.yaml")):
            raw = f.read_bytes()
            spec = ModeSpec.model_validate({**yaml.safe_load(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            if spec.mode in specs:
                raise ValueError(f"duplicate mode {spec.mode}")
            specs[spec.mode] = spec
        if set(specs) != set(FloodMode):
            raise ValueError(f"mode definitions missing: {sorted(set(FloodMode) - set(specs))}")
        cls._specs = specs
        return specs

    @classmethod
    def get(cls, mode: FloodMode | str) -> ModeSpec:
        if not cls._specs:
            cls.load_all()
        return cls._specs[FloodMode(mode)]

    @classmethod
    def all(cls) -> list[ModeSpec]:
        return [cls.get(m) for m in FloodMode]


def effective_mode(db: Session, district: District) -> FloodMode:
    """Never read district.flood_mode directly in scoring: replays use the snapshot taken at start."""
    sid = current_scenario()
    if sid is not None:
        sc = db.get(ReplayScenario, sid)
        snap = ((sc.config if sc else None) or {}).get("modes", {})
        if (m := snap.get(str(district.id))) is not None:
            return FloodMode(m)
    return FloodMode(district.flood_mode)


def snapshot_modes(db: Session, district_ids: list[UUID]) -> dict[str, str]:
    rows = db.execute(select(District.id, District.flood_mode).where(District.id.in_(district_ids)))
    return {str(i): m for i, m in rows}


def effective_from(db: Session) -> date:
    from app.models.forecasting import RiskIndex

    today = get_clock().today()
    ran = db.scalar(select(func.count()).select_from(RiskIndex)
                    .where(RiskIndex.run_date == today, RiskIndex.scenario_id.is_(None)))
    return today + timedelta(days=1) if ran else today


def set_district_mode(db: Session, district_id: UUID, mode: str, reason: str, actor: str) -> dict[str, Any]:
    if current_scenario() is not None:
        raise Conflict("MODE_LOCKED_IN_REPLAY", "Modes are fixed for a running replay.")
    try:
        new = FloodMode(mode)
    except ValueError:
        raise Unprocessable("INVALID_MODE", f"Mode must be one of {', '.join(FloodMode)}.") from None
    if len((reason or "").strip()) < 10:
        raise Unprocessable("REASON_REQUIRED", "Give a reason of at least 10 characters.")
    d = db.get(District, district_id, with_for_update=True)
    if d is None:
        raise NotFound("DISTRICT_NOT_FOUND", "District not found.")
    prev = FloodMode(d.flood_mode)
    out: dict[str, Any] = {"district_id": d.id, "flood_mode": new, "previous_mode": prev, "changed": prev != new,
                           "effective_from": effective_from(db), "audit_seq": None}
    if prev == new:
        return out
    d.flood_mode = new.value
    entry = get_audit().record(db, event_type="DISTRICT_FLOOD_MODE_CHANGED", entity_type="District",
                               entity_id=d.id, actor=actor,
                               payload={"district_id": str(d.id), "from": prev.value, "to": new.value,
                                        "reason": reason.strip()})
    db.commit()
    out["audit_seq"] = entry.seq
    return out


def load_mode_seed(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load(Path(path or settings.FLOOD_MODE_SEED).read_text(encoding="utf-8"))


def apply_flood_mode_seed(db: Session, path: Path | None = None) -> int:
    cfg = load_mode_seed(path)
    defaults = cfg["province_defaults"]
    overrides = {o["pcode"]: o["mode"] for o in cfg.get("overrides") or []}
    n = 0
    for d in db.scalars(select(District)):
        mode = FloodMode(overrides.get(d.pcode) or defaults.get(d.province, "SLOW_ONSET")).value
        if d.flood_mode != mode:
            d.flood_mode = mode
            n += 1
    db.flush()
    return n
