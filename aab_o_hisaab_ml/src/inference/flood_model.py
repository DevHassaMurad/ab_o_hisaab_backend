"""Stage 9: Aab-o-Hisaab AI Integration Module (FloodForecast + RiskIndex + TriggerTier).

Implements the exact class architecture from Page 8 & UC04 of the Aab-o-Hisaab AI
Project Document while keeping the ML model's probability output cleanly separated
from the PDMA's funding-tier rules (FR3, FR4, FR6, FR7, FR8):

  1. FloodForecastModel.predict_proba(features_df) -> float probability in [0.0, 1.0]
  2. RiskEngine.probability_to_score(prob, flood_mode) -> 0 to 100 risk score
  3. RiskEngine.score_to_tier(score, thresholds) -> Watch (40+), Warning (60+), Action (80+)

When run as a script (`python src/inference/flood_model.py`), it executes the
three backtests required by the Project Document (Page 2, FR5, NFR3):
  - Sindh August 2022 (slow-onset flood backtest)
  - KP / Buner July-August 2025 (flash-flood backtest, corroborated with FAO EVE satellite data)
  - Monsoon 2021 control year (verifying no false Action trigger)
and writes `reports/backtest_integration_results.json`.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"


@dataclass(frozen=True)
class TriggerTierRule:
    """PDMA configurable tier rule (UC01 / FR6-FR8) — kept separate from ML model."""
    watch_min: int = 40
    warning_min: int = 60
    action_min: int = 80
    watch_release_pct: float = 0.10
    warning_release_pct: float = 0.30
    action_release_pct: float = 0.60


class FloodForecastModel:
    """Wraps the trained C02 rare-event flood classifier (outputs pure probabilities)."""

    def __init__(self, model_path: Path = MODELS / "flood_model.joblib"):
        bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.features: list[str] = bundle["features"]
        self.name: str = bundle["name"]
        self.threshold: float = float(bundle["threshold"])

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Return P(qualifying flood in next 7 days) in [0.0, 1.0] for each district-day row."""
        missing = [c for c in self.features if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required feature columns: {missing}")
        return self.model.predict_proba(df[self.features])[:, 1]

    def explain_decision(self, df_row: pd.DataFrame, top_k: int = 8) -> dict:
        """Challenge C06 Regulatory XAI: Compute exact TreeSHAP attributions for a single district-day."""
        import shap
        if len(df_row) != 1:
            df_row = df_row.iloc[[0]]
        X = df_row[self.features]
        prob = float(self.predict_proba(df_row)[0])
        explainer = shap.TreeExplainer(self.model)
        sv_raw = explainer.shap_values(X)
        if isinstance(sv_raw, list):
            sv = np.asarray(sv_raw[1])[0]
            base_val = float(np.asarray(explainer.expected_value)[1])
        elif sv_raw.ndim == 3:
            sv = sv_raw[0, :, 1]
            base_val = float(np.asarray(explainer.expected_value)[1])
        else:
            sv = sv_raw[0]
            base_val = float(np.asarray(explainer.expected_value).ravel()[0])
        order = np.argsort(np.abs(sv))[::-1][:top_k]
        drivers = [
            {
                "feature": self.features[i],
                "value": round(float(X.iloc[0, i]), 4),
                "shap_delta_prob": round(float(sv[i]), 4),
                "direction": "increases_risk" if sv[i] >= 0 else "decreases_risk",
            }
            for i in order
        ]
        return {
            "base_prior_prob": round(base_val, 4),
            "predicted_prob": round(prob, 4),
            "threshold": round(self.threshold, 4),
            "approved": bool(prob >= self.threshold),
            "top_shap_drivers": drivers,
        }


class RiskEngine:
    """Converts model probabilities into 0-100 risk scores and PDMA funding tiers."""

    def __init__(self, rules: TriggerTierRule = TriggerTierRule()):
        self.rules = rules

    @staticmethod
    def probability_to_score(prob: np.ndarray | float) -> np.ndarray | float:
        """Map flood probability [0, 1] to integer/float 0-100 risk score."""
        return np.round(np.clip(np.asarray(prob, dtype=float) * 100.0, 0.0, 100.0), 1)

    def score_to_tier(self, score: float) -> dict:
        """Map a 0-100 risk score to PDMA tier, release %, and DC approval requirement."""
        if score >= self.rules.action_min:
            return {"tier": "Action", "release_pct": self.rules.action_release_pct,
                    "requires_dc_approval": True}
        if score >= self.rules.warning_min:
            return {"tier": "Warning", "release_pct": self.rules.warning_release_pct,
                    "requires_dc_approval": False}
        if score >= self.rules.watch_min:
            return {"tier": "Watch", "release_pct": self.rules.watch_release_pct,
                    "requires_dc_approval": False}
        return {"tier": "Normal", "release_pct": 0.0, "requires_dc_approval": False}


def run_backtests():
    fm = FloodForecastModel()
    engine = RiskEngine()
    df = pd.read_parquet(PROC / "dataset.parquet").dropna(subset=fm.features)
    df["prob"] = fm.predict_proba(df)
    df["risk_score"] = engine.probability_to_score(df["prob"])
    df["tier"] = [engine.score_to_tier(s)["tier"] for s in df["risk_score"]]

    # 1. Sindh August 2022 backtest (Dadu / Larkana / Shikarpur / Jacobabad)
    s22 = df[(df["province"] == "Sindh") & (df["date"].between("2022-08-05", "2022-08-25"))]
    dadu22 = s22[s22["district_name"] == "Dadu"][
        ["date", "district_name", "flood_mode", "rain_7d", "gwettop", "gwetroot",
         "prob", "risk_score", "tier", "flood_next_7d"]
    ]
    sindh_peak = (s22.groupby("district_name")
                  .agg(max_score=("risk_score", "max"),
                       max_prob=("prob", "max"),
                       first_watch=("date", lambda s: str(s[s22.loc[s.index, "risk_score"] >= 40].min().date())
                                    if (s22.loc[s.index, "risk_score"] >= 40).any() else None),
                       first_action=("date", lambda s: str(s[s22.loc[s.index, "risk_score"] >= 80].min().date())
                                     if (s22.loc[s.index, "risk_score"] >= 80).any() else None))
                  .sort_values("max_score", ascending=False))

    # 2. KP / Monsoon 2025 flash-flood backtest (Buner / Swat / Shangla / Mansehra / Haripur)
    kp25 = df[(df["province"] == "Khyber Pakhtunkhwa") &
              (df["date"].between("2025-07-01", "2025-08-31"))]
    kp_peak = (kp25.groupby("district_name")
               .agg(max_score=("risk_score", "max"),
                    max_prob=("prob", "max"),
                    max_rain_7d=("rain_7d", "max"),
                    max_gwettop=("gwettop", "max"))
               .sort_values("max_score", ascending=False))

    # 3. 2021 Control Year in Sindh (Dadu / Larkana / Jacobabad during monsoon Jun-Sep 2021)
    ctrl21 = df[(df["province"] == "Sindh") & (df["date"].between("2021-06-01", "2021-09-30"))]
    ctrl_summary = {
        "sindh_2021_monsoon_district_days": int(len(ctrl21)),
        "action_tier_triggers": int((ctrl21["risk_score"] >= 80).sum()),
        "warning_tier_triggers": int((ctrl21["risk_score"] >= 60).sum()),
        "watch_tier_triggers": int((ctrl21["risk_score"] >= 40).sum()),
        "mean_risk_score_2021": round(float(ctrl21["risk_score"].mean()), 2),
        "p95_risk_score_2021": round(float(ctrl21["risk_score"].quantile(0.95)), 2),
    }

    out = {
        "model_used": fm.name,
        "validation_threshold": round(fm.threshold, 4),
        "sindh_aug_2022_top_districts": sindh_peak.head(10).to_dict(orient="index"),
        "dadu_aug_2022_daily_progression": [
            {k: (str(v.date()) if hasattr(v, "date") else round(v, 3) if isinstance(v, float) else v)
             for k, v in r.items()}
            for r in dadu22.to_dict(orient="records")
        ],
        "kp_monsoon_2025_top_districts": kp_peak.head(10).to_dict(orient="index"),
        "control_year_2021_sindh_monsoon": ctrl_summary,
    }
    (REPORTS / "backtest_integration_results.json").write_text(json.dumps(out, indent=2))
    print("=== Stage 9 Integration & Backtest Summary ===")
    print("Model:", fm.name)
    print("\nSindh Aug 2022 Top 8 Districts:\n", sindh_peak.head(8).to_string())
    print("\nDadu Aug 2022 Daily Replay (Aug 10-22):\n",
          dadu22[dadu22["date"].between("2022-08-10", "2022-08-22")].to_string(index=False))
    print("\nKP Monsoon 2025 Top 8 Districts:\n", kp_peak.head(8).to_string())
    print("\n2021 Control Year (Sindh Monsoon):", json.dumps(ctrl_summary, indent=2))


if __name__ == "__main__":
    run_backtests()
