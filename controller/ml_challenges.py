"""Hackathon challenges C02 / C06 / C12, served from ml_engine (the team's ML project inside this backend).

Historical results come straight from ml_engine/reports (produced by ml_engine/src/...). Two parts are LIVE on today's
data:
  C06 live: exact TreeSHAP (shap.TreeExplainer on the same random forest) for the 39-feature row the model scored for
            a district in the latest run.
  C12 live: cross-check of two independent rainfall measurements for a district - NASA POWER (MERRA-2, the model's
            input) vs NASA GPM IMERG Early Run - plus satellite/agency flood reports (NASA EONET). Same tolerance rule
            as ml_engine/src/verification/c12_verification_engine.py. Sentinel-1 SAR inundation and field text bulletins
            are not available live, and are reported as unavailable rather than invented.
"""
import csv
import json
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from controller.authentication import AuthFlowError
from controller.forecasting import IMERG_SOURCE, ML, LABELS, category, load_model, today_pkt
from model.forecast_model import DistrictRiskAssessment, ForecastDistrict, HazardEvent, HazardObservation

REPORTS = ML / "reports"
IMAGES = {
    "c02-dashboard": "c02_results_dashboard.png",
    "c02-final": "rf_final_results.png",
    "c02-pr-curve": "test_pr_curve_confusion.png",
    "c06-shap": "c06_shap_edge_cases.png",
    "c12-matrix": "c12_multimodal_verification.png",
}
FEATURE_LABELS = {
    "gwettop_rel_q99": "Surface soil wetness vs district Q99", "gwetroot_rel_q99": "Root-zone wetness vs district Q99",
    "gwetprof_rel_q99": "Deep-profile wetness vs district Q99", "gwettop": "Surface soil wetness",
    "gwetroot": "Root-zone soil wetness", "gwetprof": "Deep-profile soil wetness", "rain_1d": "Rain, last 1 day (mm)",
    "rain_3d": "Rain, last 3 days (mm)", "rain_7d": "Rain, last 7 days (mm)", "rain_14d": "Rain, last 14 days (mm)",
    "rain_30d": "Rain, last 30 days (mm)", "rain_7d_anom": "7-day rain vs district normal",
    "rain_30d_anom": "30-day rain vs district normal", "prov_gwetroot": "Province root-zone wetness",
    "prov_rain_7d": "Province rain, last 7 days", "gwetroot_anom": "Root-zone wetness above Q99",
    "t2m_max": "Max temperature (°C)", "doy_sin": "Season (day of year, sine)", "doy_cos": "Season (day of year, cosine)",
    "hist_floods_train": "Historical floods 2000-2014", "area_km2": "District area (km²)",
}


def _read(name):
    p = REPORTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def image_path(key):
    name = IMAGES.get(key)
    p = REPORTS / name if name else None
    if p is None or not p.exists():
        raise AuthFlowError("IMAGE_NOT_FOUND", "This chart is not available.", 404)
    return p


# Historical reports -----------------------------------------------------------------------------------
def challenges_overview():
    t = _read("test_results.json") or {}
    splits = _read("splits_summary.json") or {}
    comparison = []
    p = REPORTS / "model_comparison_validation.csv"
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                comparison.append({"model": r["model"], "val99_pr_auc": float(r["val99_pr_auc"]),
                                   "val_natural_pr_auc": float(r["val_natural_pr_auc"]),
                                   "val99_best_f1": float(r["val99_best_f1"]) if r.get("val99_best_f1") else None})
    c06 = _read("c06_xai_edge_cases_audit.json")
    c12 = _read("c12_verification_report.json")
    backtest = _read("backtest_integration_results.json")
    return {
        "c02": {
            "title": "C02 - Severe class imbalance (99:1 rare-event stress)",
            "status": "PASS" if t.get("pr_auc_average_precision", 0) >= 0.70 else "FAIL",
            "requirement": "PR-AUC >= 0.70 on a 99:1 negative:positive evaluation split",
            "pr_auc": t.get("pr_auc_average_precision"), "precision": t.get("precision"), "recall": t.get("recall"),
            "f1": t.get("f1"), "confusion_matrix": t.get("confusion_matrix"), "test_rows": t.get("test_rows"),
            "test_ratio": t.get("test_ratio"), "pr_auc_by_flood_mode": t.get("pr_auc_by_flood_mode"),
            "recall_by_days_to_flood": t.get("recall_by_days_to_flood"),
            "events": {"in_test": t.get("events_in_test"), "detected": t.get("events_detected_at_least_once"),
                       "district_events": t.get("district_events_detected")},
            "majority_class_baseline": t.get("all_negative_predictor"), "splits": splits,
            "model_comparison": sorted(comparison, key=lambda r: -r["val99_pr_auc"]),
            "images": ["c02-dashboard", "c02-final", "c02-pr-curve"],
        },
        "c06": {
            "title": "C06 - Explainability and feature attribution (TreeSHAP audit)",
            "explainer": (c06 or {}).get("explainer"), "base_prior_probability": (c06 or {}).get("base_prior_probability"),
            "decision_threshold": (c06 or {}).get("decision_threshold"), "edge_cases": (c06 or {}).get("edge_cases", []),
            "images": ["c06-shap"], "available": c06 is not None,
        },
        "c12": {
            "title": "C12 - Multimodal consistency cross-check",
            "architecture": (c12 or {}).get("architecture"), "scenarios": (c12 or {}).get("scenarios", []),
            "images": ["c12-matrix"], "available": c12 is not None,
        },
        "backtests": {
            "sindh_aug_2022": (backtest or {}).get("sindh_aug_2022_top_districts"),
            "control_year_2021": (backtest or {}).get("control_year_2021_sindh_monsoon"),
        },
        "source": "ml_engine/reports (generated by ml_engine/src/models/evaluate.py, explain_c06.py and "
                  "verification/c12_verification_engine.py)",
    }


# C06 live -----------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _explainer():
    import shap

    return shap.TreeExplainer(load_model()["model"])


def _latest(db, pcode):
    d = db.get(ForecastDistrict, pcode)
    if d is None:
        raise AuthFlowError("DISTRICT_NOT_FOUND", "This district does not exist.", 404)
    a = db.scalar(select(DistrictRiskAssessment).where(DistrictRiskAssessment.district_pcode == pcode)
                  .order_by(DistrictRiskAssessment.id.desc()).limit(1))
    return d, a


def explain_district(db, pcode, top_k=10):
    d, a = _latest(db, pcode)
    feats = (a.calculation_details or {}).get("features") if a else None
    if not feats:
        return {"available": False, "district": d.name, "reason": "No complete scored feature row yet. It is stored "
                                                                  "from the next data refresh onward."}
    bundle = load_model()
    names = bundle["features"]
    X = pd.DataFrame([[feats[n] for n in names]], columns=names)
    prob = float(bundle["model"].predict_proba(X)[:, 1][0])
    ex = _explainer()
    sv = ex.shap_values(X)
    if isinstance(sv, list):
        vals, base = np.asarray(sv[1])[0], float(np.asarray(ex.expected_value)[1])
    elif np.asarray(sv).ndim == 3:
        vals, base = np.asarray(sv)[0, :, 1], float(np.asarray(ex.expected_value)[1])
    else:
        vals, base = np.asarray(sv)[0], float(np.asarray(ex.expected_value).ravel()[0])
    order = np.argsort(np.abs(vals))[::-1]
    drivers = [{"feature": names[i], "label": FEATURE_LABELS.get(names[i], names[i].replace("_", " ")),
                "value": round(float(X.iloc[0, i]), 4), "shap_delta_prob": round(float(vals[i]), 4),
                "direction": "increases_risk" if vals[i] >= 0 else "decreases_risk"} for i in order[:top_k]]
    rest = float(sum(vals[i] for i in order[top_k:]))
    score = round(prob * 100, 1)
    return {
        "available": True, "district": d.name, "province": d.province, "as_of_date": a.as_of_date.isoformat(),
        "model_version": a.model_version, "base_prior_probability": round(base, 4), "predicted_probability": round(prob, 4),
        "risk_score": score, "risk_category": category(score), "risk_label": LABELS[category(score)],
        "check_sum": round(base + float(vals.sum()), 4), "drivers": drivers,
        "other_features_combined": round(rest, 4),
        "method": "Exact TreeSHAP (shap.TreeExplainer) over the random forest. Base prior + all SHAP contributions "
                  "= predicted probability.",
    }


# C12 live -----------------------------------------------------------------------------------------------
def _within_rain_tolerance(a, b):  # identical rule to ml_engine C12 engine
    diff = abs(a - b)
    return diff <= 22.0 or diff / max(b, 10.0) <= 0.25


def verify_district(db, pcode):
    d, a = _latest(db, pcode)
    inputs = (a.calculation_details or {}).get("inputs", {}) if a else {}
    power_7d = inputs.get("rain_7d")
    as_of = a.as_of_date if a else None
    imerg = []
    if as_of:
        imerg = db.execute(select(HazardObservation.observation_date, HazardObservation.value).where(
            HazardObservation.district_pcode == pcode, HazardObservation.source == IMERG_SOURCE,
            HazardObservation.variable == "precipitation",
            HazardObservation.observation_date > as_of - timedelta(days=7),
            HazardObservation.observation_date <= as_of)).all()
    imerg_7d = round(sum(v for _, v in imerg), 2) if len(imerg) == 7 else None
    since = datetime.utcnow() - timedelta(days=14)
    reports = [{"title": e.title, "category": e.category, "event_date": e.event_date.isoformat() + "Z" if e.event_date else None,
                "link": e.link} for e in db.scalars(select(HazardEvent).where(
                    HazardEvent.district_pcode == pcode, HazardEvent.event_date >= since))]
    flood_reported = any(r["category"] == "flood" for r in reports)
    checks = []
    if power_7d is not None and imerg_7d is not None:
        ok = _within_rain_tolerance(float(power_7d), float(imerg_7d))
        checks.append({"pair": "NASA POWER vs GPM IMERG (7-day rain)", "result": "CONSISTENT" if ok else "CONTRADICTION",
                       "detail": f"{power_7d:.1f} mm vs {imerg_7d:.1f} mm (tolerance: within 22 mm or 25%)"})
    else:
        checks.append({"pair": "NASA POWER vs GPM IMERG (7-day rain)", "result": "UNAVAILABLE",
                       "detail": "IMERG does not cover all 7 days ending on the model's observation date."})
    score = a.risk_score if a else None
    high = score is not None and score >= 80
    if high:
        checks.append({"pair": "High model score vs reported flood events (NASA EONET)",
                       "result": "CONSISTENT" if flood_reported else "NO_REPORT",
                       "detail": "A flood event is reported for this district in the last 14 days." if flood_reported
                       else "No satellite/agency flood report yet; anticipatory scores often precede reports."})
    verdicts = {c["result"] for c in checks}
    if "CONTRADICTION" in verdicts:
        result = "CONTRADICTION_DETECTED"
    elif verdicts <= {"UNAVAILABLE"}:
        result = "INSUFFICIENT_DATA"
    elif "UNAVAILABLE" in verdicts or "NO_REPORT" in verdicts:
        result = "PARTIALLY_VERIFIED"
    else:
        result = "CONSISTENT"
    tier = category(score) if score is not None else "insufficient"
    return {
        "district": d.name, "province": d.province, "as_of_date": as_of.isoformat() if as_of else None,
        "risk_score": score, "risk_category": tier, "risk_label": LABELS[tier],
        "modalities": {
            "structured_telemetry": {"source": "NASA POWER (MERRA-2)", "rain_7d_mm": power_7d,
                                     "rain_30d_mm": inputs.get("rain_30d"), "available": power_7d is not None},
            "independent_satellite_rain": {"source": IMERG_SOURCE, "rain_7d_mm": imerg_7d, "days": len(imerg),
                                           "available": imerg_7d is not None},
            "sar_inundation": {"source": "Sentinel-1 SAR / UN FAO EVE", "available": False,
                               "note": "Not available for live checks in this deployment."},
            "event_reports": {"source": "NASA EONET (last 14 days)", "available": True, "reports": reports},
            "field_text_reports": {"source": "PDMA/NDMA bulletins", "available": False,
                                   "note": "No live bulletin feed connected."},
        },
        "checks": checks, "verification_result": result,
        "funding_gate": ("Action-tier release may proceed to DC approval" if high and result == "CONSISTENT"
                         else "Hold for human review" if high else "No action-tier release (score below 80)"),
        "method": "Pairwise cross-check using the ml_engine C12 rain tolerance (|a-b| <= 22 mm or <= 25%).",
    }
