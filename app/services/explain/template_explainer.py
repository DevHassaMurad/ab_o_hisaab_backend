"""D2 template explanations for risk scores. English is built in; Urdu comes from a translation file written by a
native speaker (constitution D8). Until that file exists explanation_ur is left empty. Every number in the text
comes from the stored inputs (FR3-014)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.services.forecasting.features import PERCENT_FEATURES

TRANSLATIONS = Path(__file__).with_name("translations") / "ur.yaml"

TIER_EN = {"NONE": "Normal", "WATCH": "Watch", "WARNING": "Warning", "ACTION": "Action"}
EN_MAIN = "{district}: flood risk {score}/100 ({tier}). Highest in {lead} day(s), on {date}."
EN_REASONS = "Main reasons: {}."
FACTOR_EN = {
    "rain_3d_mm": "{v} mm of rain over 3 days",
    "rain_7d_mm": "{v} mm of rain over 7 days",
    "rain_14d_mm": "{v} mm of rain over 14 days",
    "rain_30d_mm": "{v} mm of rain over 30 days",
    "rain_fcst_target_mm": "{v} mm of rain forecast that day",
    "rain_24h_fcst_mm": "{v} mm of rain forecast in the next 24 h",
    "rain_48h_fcst_mm": "{v} mm of rain forecast in the next 48 h",
    "max_3h_fcst_mm": "bursts of up to {v} mm in 3 hours",
    "max_3h_obs_mm": "bursts of up to {v} mm in 3 hours",
    "soil_wetness_root": "soil already {v}% wet",
    "soil_wetness_top": "soil already {v}% wet",
    "slope_mean_deg": "steep slopes ({v} degrees) make water run fast",
    "elevation_mean_m": "low-lying land ({v} m)",
    "vulnerability_weight": "many vulnerable households (index {v}%)",
}
NOTICE_EN = {
    "fallback": "Model unavailable: score from the rainfall rule.",
    "stale": "Some data is old (last valid {date}).",
    "proxy": "Replay: forecast rain is a proxy built from observed data.",
}


@dataclass(frozen=True)
class RiskExplainInputs:
    district_name: str
    score: int
    tier: str
    peak_lead_day: int
    target_date: date
    top_factors: list[dict[str, Any]]
    is_fallback: bool
    is_stale_input: bool
    stale_since: date | None
    forecast_is_proxy: bool


def fmt(factor: dict[str, Any]) -> str:
    v = factor["value"]
    return str(round(v * 100)) if factor["factor"] in PERCENT_FEATURES else str(round(v))


class TemplateExplainService:
    def __init__(self) -> None:
        self.ur = yaml.safe_load(TRANSLATIONS.read_text(encoding="utf-8")) if TRANSLATIONS.exists() else None

    def explain_risk(self, i: RiskExplainInputs) -> tuple[str, str | None]:
        en = [EN_MAIN.format(district=i.district_name, score=i.score, tier=TIER_EN[i.tier], lead=i.peak_lead_day,
                             date=i.target_date.strftime("%d %b"))]
        f = [x for x in i.top_factors[:3] if x["factor"] in FACTOR_EN]
        if f:
            en.append(EN_REASONS.format(", ".join(FACTOR_EN[x["factor"]].format(v=fmt(x)) for x in f)))
        stale = i.stale_since.strftime("%d %b") if i.stale_since else "unknown"
        for flag, key in ((i.is_fallback, "fallback"), (i.is_stale_input, "stale"), (i.forecast_is_proxy, "proxy")):
            if flag:
                en.append(NOTICE_EN[key].format(date=stale))
        return " ".join(en), self._urdu(i, f)

    def _urdu(self, i: RiskExplainInputs, f: list[dict[str, Any]]) -> str | None:
        if not self.ur:
            return None
        t = self.ur
        parts = [t["main"].format(district=i.district_name, score=i.score, tier=t["tiers"][i.tier],
                                  lead=i.peak_lead_day, date=i.target_date.strftime("%d-%m"))]
        if f:
            parts.append(t["reasons"].format(t["separator"].join(t["factors"][x["factor"]].format(v=fmt(x))
                                                                 for x in f)))
        for flag, key in ((i.is_fallback, "fallback"), (i.is_stale_input, "stale"), (i.forecast_is_proxy, "proxy")):
            if flag:
                parts.append(t["notices"][key].format(date=i.stale_since.strftime("%d-%m") if i.stale_since else ""))
        return " ".join(parts)
