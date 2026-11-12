"""Challenge C06 — Explainability & Feature Attribution (XAI / Regulatory Audit).

Generates exact TreeSHAP feature-attribution explanations, JSON audit trail,
and a 3-panel regulatory waterfall / attribution chart for 3 edge-case decisions:
  1. Edge Case 1 (Counter-Intuitive Approval — Slow Riverine Saturation):
     Sujawal, Sindh (2022-08-04) — Only 2.57 mm 7-day rain, yet APPROVED for Action Tier
     (Risk Score = 91.8 / 100, 60% Payout) because soil wetness & 30-day monsoon
     accumulation exceeded the 99th-percentile saturation threshold.
  2. Edge Case 2 (Counter-Intuitive Rejection — Heavy Rain on Arid Unsaturated Soil):
     Mirpur Khas, Sindh (2021-05-22) — 244.28 mm 7-day unseasonal storm burst, yet REJECTED
     (Risk Score = 16.1 / 100, 0% Payout) because pre-storm root-zone & surface soil
     wetness were far below saturation capacity (gwettop = 0.33, 44% of Q99), absorbing runoff.
  3. Edge Case 3 (Borderline Threshold Decision — Early Watch Tier Trigger):
     Muzaffarabad, Azad Kashmir (2021-04-13) — Moderate 7-day rain (12.39 mm) right on the
     decision boundary (Risk Score = 44.9 / 100, crossing the 34.2% ML threshold & 40+ Watch
     tier for 10% early preparedness release) driven by root-zone saturation + snowmelt/spring wetness.
"""
from pathlib import Path
import json
import shutil

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import shap

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ROOT / "data" / "processed" / "splits"
REPORTS = ROOT / "reports"
ARTIFACT_DIR = Path(r"C:\Users\DELL\.gemini\antigravity\brain\efd15652-3c0b-4f90-9817-1a11c8fbef11")

FEATURE_LABELS = {
    "gwettop_rel_q99": "Surface Soil Wetness vs Q99 (gwettop_rel_q99)",
    "gwetroot_rel_q99": "Root-Zone Wetness vs Q99 (gwetroot_rel_q99)",
    "gwetprof_rel_q99": "Deep Profile Wetness vs Q99 (gwetprof_rel_q99)",
    "gwettop": "Surface Soil Wetness (gwettop)",
    "gwetroot": "Root-Zone Soil Wetness (gwetroot)",
    "gwetprof": "Profile Soil Wetness (gwetprof)",
    "gwetroot_anom": "Root-Zone Wetness Z-Anomaly (gwetroot_anom)",
    "rain_30d": "30-Day Cumulative Rainfall (rain_30d)",
    "rain_30d_anom": "30-Day Rain Z-Anomaly (rain_30d_anom)",
    "rain_14d": "14-Day Cumulative Rainfall (rain_14d)",
    "rain_7d": "7-Day Cumulative Rainfall (rain_7d)",
    "rain_7d_anom": "7-Day Rain Z-Anomaly (rain_7d_anom)",
    "rain_3d": "3-Day Cumulative Rainfall (rain_3d)",
    "rain_1d": "1-Day Rainfall (rain_1d)",
    "prov_gwetroot": "Provincial Mean Root Wetness (prov_gwetroot)",
    "prov_gwettop": "Provincial Mean Surface Wetness (prov_gwettop)",
    "prov_gwetprof": "Provincial Mean Profile Wetness (prov_gwetprof)",
    "prov_rain_7d": "Provincial 7-Day Rain (prov_rain_7d)",
    "prov_rain_3d": "Provincial 3-Day Rain (prov_rain_3d)",
    "rh2m": "Relative Humidity 2m (rh2m)",
    "t2m": "Air Temperature 2m (t2m)",
    "t2m_max": "Max Daily Temp (t2m_max)",
    "t2m_max_7d": "7-Day Max Temp (t2m_max_7d)",
    "gwettop_chg_3d": "3-Day Surface Wetness Change (gwettop_chg_3d)",
    "gwettop_chg_7d": "7-Day Surface Wetness Change (gwettop_chg_7d)",
    "gwetroot_chg_7d": "7-Day Root Wetness Change (gwetroot_chg_7d)",
    "gwetprof_chg_7d": "7-Day Profile Wetness Change (gwetprof_chg_7d)",
}


def get_tier_decision(prob: float, thr: float) -> dict:
    score = round(prob * 100.0, 1)
    if score >= 80.0:
        return {
            "ml_binary_decision": "APPROVED (Flood Alert = 1)",
            "funding_tier": "ACTION TIER (Score >= 80)",
            "payout_release_pct": 60,
            "regulatory_action": "Release 60% Parametric Payout (with DC Confirmation)",
        }
    elif score >= 60.0:
        return {
            "ml_binary_decision": "APPROVED (Flood Alert = 1)",
            "funding_tier": "WARNING TIER (Score 60–79)",
            "payout_release_pct": 30,
            "regulatory_action": "Release 30% Pre-Positioning Funds",
        }
    elif score >= 40.0 or prob >= thr:
        return {
            "ml_binary_decision": "APPROVED (Flood Alert = 1)",
            "funding_tier": "WATCH TIER (Score 40–59 / Above ML Threshold)",
            "payout_release_pct": 10,
            "regulatory_action": "Release 10% Early Preparedness Tranche",
        }
    else:
        return {
            "ml_binary_decision": "REJECTED (No Flood = 0)",
            "funding_tier": "NORMAL / NO TRIGGER (Score < 40)",
            "payout_release_pct": 0,
            "regulatory_action": "Hold Funds (0% Disbursement — Soil Capacity Absorbs Rain)",
        }


def main():
    art = joblib.load(ROOT / "models" / "flood_model.joblib")
    model, feats, thr = art["model"], art["features"], float(art["threshold"])

    te = pd.read_parquet(SPLITS / "test_99to1.parquet").dropna(subset=feats).copy()
    te["prob"] = model.predict_proba(te[feats])[:, 1]

    # Select the 3 real historical edge cases from the untouched test split
    case_specs = [
        {
            "case_id": "Edge Case 1 — Low 7-Day Local Rain, High Slow-Flood Approval",
            "short_title": "Edge Case 1: Low Local Rain (2.6mm) -> APPROVED (Action 91.8/100)",
            "subtitle": "Sujawal, Sindh (2022-08-04, Slow Riverine Mode) | Actual Outcome: Flood = 1",
            "row": te[(te["district_name"] == "Sujawal") & (te["date"] == "2022-08-04")].iloc[0],
            "why_edge_case": (
                "Only 2.57 mm of local rain fell in the prior 7 days, which a naive rainfall rule "
                "would reject. However, 407.1 mm of 30-day monsoon rain and upstream Indus basin "
                "inflows pushed Sujawal's surface and root-zone soil wetness to 102.8% and 105.5% "
                "of its 99th-percentile threshold, triggering an Action Tier approval (91.8/100)."
            ),
        },
        {
            "case_id": "Edge Case 2 — Extreme 7-Day Rain Burst on Dry Soil -> Rejected",
            "short_title": "Edge Case 2: Heavy Rain (244.3mm) on Dry Soil -> REJECTED (16.1/100)",
            "subtitle": "Mirpur Khas, Sindh (2021-05-22, Slow Mode) | Actual Outcome: No Flood = 0",
            "row": te[(te["district_name"] == "Mirpur Khas") & (te["date"] == "2021-05-22")].iloc[0],
            "why_edge_case": (
                "An extreme 244.28 mm 7-day pre-monsoon cloudburst hit Mirpur Khas in May 2021. "
                "A simple rainfall threshold would falsely trigger a payout, but the model REJECTS "
                "the payout (Risk = 16.1/100 < 34.2% threshold) because pre-storm soil wetness was "
                "extremely dry (gwettop = 0.33, just 44% of Q99), allowing the parched ground to absorb the rain."
            ),
        },
        {
            "case_id": "Edge Case 3 — Borderline Threshold Decision (Watch Tier Approval)",
            "short_title": "Edge Case 3: Borderline Risk (44.9/100 vs 34.2% Thr) -> WATCH TIER",
            "subtitle": "Muzaffarabad, AJK (2021-04-13, Flash Mode) | Actual Outcome: Flood = 1",
            "row": te[(te["district_name"] == "Muzaffarabad") & (te["date"] == "2021-04-13")].iloc[0],
            "why_edge_case": (
                "7-day rainfall was modest (12.39 mm) and the model probability (0.4487) sits just "
                "above the 0.3419 validation threshold (Watch Tier: 10% early preparedness release). "
                "SHAP shows that root-zone saturation exceeding Q99 (101.3%) and 30-day cumulative "
                "spring precipitation (174.4 mm) tipped the decision from Hold into Watch."
            ),
        },
    ]

    X_cases = pd.DataFrame([c["row"][feats].values for c in case_specs], columns=feats)

    # Compute exact TreeSHAP values for positive class (Flood = 1)
    explainer = shap.TreeExplainer(model)
    shap_vals_raw = explainer.shap_values(X_cases)
    if isinstance(shap_vals_raw, list):
        shap_pos = np.asarray(shap_vals_raw[1])
        base_val = float(np.asarray(explainer.expected_value)[1])
    elif shap_vals_raw.ndim == 3:
        shap_pos = shap_vals_raw[:, :, 1]
        base_val = float(np.asarray(explainer.expected_value)[1])
    else:
        shap_pos = shap_vals_raw
        base_val = float(np.asarray(explainer.expected_value).ravel()[0])

    audit_records = []
    for idx, spec in enumerate(case_specs):
        r = spec["row"]
        sv = shap_pos[idx]
        prob = float(r["prob"])
        decision = get_tier_decision(prob, thr)

        # Rank features by absolute SHAP contribution
        order = np.argsort(np.abs(sv))[::-1]
        top_drivers = []
        for rank_i in order[:8]:
            fname = feats[rank_i]
            fval = float(r[fname])
            sval = float(sv[rank_i])
            top_drivers.append({
                "feature": fname,
                "label": FEATURE_LABELS.get(fname, fname),
                "feature_value": round(fval, 4),
                "shap_contribution": round(sval, 4),
                "direction": "Increases Flood Risk (+)" if sval >= 0 else "Decreases Flood Risk (-)",
            })

        audit_records.append({
            "case_id": spec["case_id"],
            "district_name": str(r["district_name"]),
            "province": str(r["province"]),
            "date": str(r["date"])[:10],
            "flood_mode": str(r["flood_mode"]),
            "actual_flood_next_7d": int(r["flood_next_7d"]),
            "base_expected_probability": round(base_val, 4),
            "predicted_probability": round(prob, 4),
            "risk_score_0_100": round(prob * 100.0, 1),
            "validation_threshold": round(thr, 4),
            **decision,
            "why_edge_case": spec["why_edge_case"],
            "top_shap_drivers": top_drivers,
        })

    out_json = REPORTS / "c06_xai_edge_cases_audit.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "challenge": "C06 — Explainability & Feature Attribution (XAI / Regulatory Audit)",
            "model": "Random Forest (rf_plain, 300 trees, max_depth=14)",
            "explainer": f"SHAP TreeExplainer (shap v{shap.__version__})",
            "base_prior_probability": round(base_val, 4),
            "decision_threshold": round(thr, 4),
            "edge_cases": audit_records,
        }, f, indent=2)

    # Create the 3-panel SHAP Feature-Attribution Visualization
    plt.rcParams["font.sans-serif"] = "DejaVu Sans"
    plt.rcParams["axes.edgecolor"] = "#CBD5E1"
    plt.rcParams["axes.linewidth"] = 0.8

    fig = plt.figure(figsize=(18, 12.5), dpi=180, facecolor="#F8FAFC")
    gs = gridspec.GridSpec(3, 1, figure=fig, hspace=0.42)

    fig.suptitle(
        "Aab-o-Hisaab AI — Challenge C06: SHAP Feature Attribution & Regulatory Audit (3 Edge-Case Decisions)",
        fontsize=15.5, fontweight="bold", color="#0F172A", y=0.975
    )
    fig.text(
        0.5, 0.947,
        f"Exact TreeSHAP Contributions from Base Prior P0 = {base_val:.3f} to Final Predicted Probability  |  ML Alert Threshold = {thr:.3f}  |  Red (+) = Pushes Toward Approval, Blue (-) = Pushes Toward Rejection",
        ha="center", fontsize=10.5, color="#1E40AF", fontweight="bold"
    )

    for idx, (spec, rec) in enumerate(zip(case_specs, audit_records)):
        ax = fig.add_subplot(gs[idx, 0])
        ax.set_facecolor("white")
        sv = shap_pos[idx]
        r = spec["row"]

        # Top 7 features + sum of remaining 28 features
        order = np.argsort(np.abs(sv))[::-1]
        top_k = 7
        top_idx = order[:top_k]
        other_shap = float(sv[order[top_k:]].sum())

        names = []
        vals = []
        for i_f in top_idx[::-1]:
            fn = feats[i_f]
            fv = float(r[fn])
            names.append(f"{fn} = {fv:.2f}")
            vals.append(float(sv[i_f]))

        names = [f"Other 28 Features (Combined)"] + names
        vals = [other_shap] + vals

        colors = ["#DC2626" if v > 0 else "#2563EB" for v in vals]
        bars = ax.barh(names, vals, color=colors, height=0.62, alpha=0.9)
        ax.axvline(0.0, color="#0F172A", linewidth=1.1)

        min_v, max_v = min(vals), max(vals)
        span = max(max_v - min_v, 0.15)
        ax.set_xlim(min_v - 0.18 * span, max_v + 0.22 * span)

        for bar, v in zip(bars, vals):
            offset = 0.012 * span if v >= 0 else -0.012 * span
            ha = "left" if v >= 0 else "right"
            sign = "+" if v >= 0 else ""
            ax.text(
                v + offset,
                bar.get_y() + bar.get_height() / 2,
                f"{sign}{v:.4f} ({sign}{v*100:.1f} pts)",
                va="center", ha=ha, fontsize=9, fontweight="bold",
                color="#991B1B" if v >= 0 else "#1E40AF"
            )

        badge_color = "#059669" if "ACTION" in rec["funding_tier"] else ("#D97706" if "WATCH" in rec["funding_tier"] else "#475569")
        ax.set_title(
            f"{spec['short_title']}   |   {spec['subtitle']}   |   Action: {rec['regulatory_action']}",
            fontsize=11.2, fontweight="bold", color=badge_color, pad=8, loc="left"
        )
        ax.set_xlabel("SHAP Contribution to Flood Probability (Delta P from Base Prior)", fontsize=9.5, fontweight="bold", color="#334155")
        ax.grid(axis="x", linestyle=":", alpha=0.6)

        # Summary callout box inside panel
        summary_txt = (
            f"Regulatory Audit Summary:\n"
            f"• Base Prior P0: {base_val:.3f} ({base_val*100:.1f}%)\n"
            f"• Net SHAP Shift: {rec['predicted_probability'] - base_val:+.3f}\n"
            f"• Final Prob P(Flood): {rec['predicted_probability']:.3f} (Score: {rec['risk_score_0_100']}/100)\n"
            f"• Decision: {rec['ml_binary_decision']}\n"
            f"• Tier: {rec['funding_tier']} ({rec['payout_release_pct']}% Payout)"
        )
        ax.text(
            0.99, 0.12, summary_txt,
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8.8, fontweight="bold", color="#0F172A",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#F1F5F9", edgecolor="#94A3B8", linewidth=1.0)
        )

    out_png = REPORTS / "c06_shap_edge_cases.png"
    plt.savefig(out_png, bbox_inches="tight", facecolor=fig.get_facecolor())
    if ARTIFACT_DIR.exists():
        shutil.copyfile(out_png, ARTIFACT_DIR / "c06_shap_edge_cases.png")
    print("Saved JSON audit to:", out_json)
    print("Saved SHAP plot to:", out_png)


if __name__ == "__main__":
    main()
