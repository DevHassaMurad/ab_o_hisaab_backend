"""FR3 model registry: loads a mode's trained artifact (ml/artifacts/<mode_dir>/<version>/). Any problem (no
artifact, missing ML libraries, feature mismatch, bad prediction) raises, and the caller uses the fallback rule.
No artifacts exist yet: training needs labelled flood events (see README)."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.forecasting.features import RAIN_FEATURES
from app.services.forecasting.modes import ModeSpec

log = logging.getLogger("aab.forecasting")


class ModelUnavailable(Exception):
    pass


class InvalidPrediction(Exception):
    pass


def transform(rows: list[dict[str, float]], features: list[str]) -> list[list[float]]:
    """log1p on rain features (shared with training)."""
    return [[math.log1p(max(0.0, r[f])) if f in RAIN_FEATURES else float(r[f]) for f in features] for r in rows]


@dataclass
class LoadedModel:
    version: str
    family: str
    features: list[str]
    model: Any
    calibrator: Any

    def predict_proba(self, rows: list[dict[str, float]]) -> list[float]:
        X = transform(rows, self.features)
        raw = self.model.predict_proba(X)[:, 1]
        p = self.calibrator.predict(raw) if hasattr(self.calibrator, "predict") else raw
        out = [float(x) for x in p]
        if any(not math.isfinite(x) or x < 0 or x > 1 for x in out):
            raise InvalidPrediction(self.version)
        return out

    def contributions(self, rows: list[dict[str, float]]) -> list[dict[str, float]]:
        X = transform(rows, self.features)
        if self.family == "lgbm":
            c = self.model.booster_.predict(X, pred_contrib=True)[:, :-1]
        else:  # sklearn Pipeline(StandardScaler, LogisticRegression)
            z = self.model[0].transform(X)
            c = z * self.model[-1].coef_[0]
        return [dict(zip(self.features, map(float, row), strict=True)) for row in c]


class ModelRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.FORECAST_ARTIFACT_DIR)
        self._cache: dict[tuple[str, str], LoadedModel] = {}

    def version_for(self, spec: ModeSpec) -> str:
        if spec.artifact_version != "current":
            return spec.artifact_version
        cur = self.root / spec.mode_dir / "CURRENT"
        if not cur.exists():
            raise ModelUnavailable(f"no trained artifact for {spec.mode} ({cur} missing)")
        return cur.read_text(encoding="utf-8").strip()

    def load(self, spec: ModeSpec) -> LoadedModel:
        version = self.version_for(spec)
        key = (spec.mode_dir, version)
        if key in self._cache:
            return self._cache[key]
        base = self.root / spec.mode_dir / version
        try:
            import joblib
        except ImportError as exc:
            raise ModelUnavailable('ML libraries not installed (pip install -e ".[ml]")') from exc
        card = json.loads((base / "model_card.json").read_text(encoding="utf-8"))
        if card["features"] != spec.features:
            raise ModelUnavailable(f"artifact {version} was trained on different features than the {spec.mode} spec")
        m = LoadedModel(version, card["family"], card["features"], joblib.load(base / "model.joblib"),
                        joblib.load(base / "calibrator.joblib"))
        self._cache[key] = m
        return m

    def info(self, spec: ModeSpec) -> dict[str, Any]:
        try:
            m = self.load(spec)
            card = json.loads((self.root / spec.mode_dir / m.version / "model_card.json").read_text())
            return {"mode": spec.mode.value, "model_version": m.version, "loaded": True, "family": m.family,
                    "metrics": card.get("metrics", {}), "holdout_years": card.get("holdout_years", [])}
        except Exception as exc:
            return {"mode": spec.mode.value, "model_version": "rules-fallback-1", "loaded": False,
                    "family": "rules", "reason": str(exc)}
