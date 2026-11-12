"""Stages 5-6: compare models + class-imbalance techniques; select on VALIDATION only.

Imbalance handling (applied to TRAINING data only):
  - none (plain model)                     -> shows the collapse risk
  - class weighting                        -> errors on rare floods cost more
  - random undersampling of negatives      -> fewer easy "no flood" rows
  - random oversampling of positives       -> repeats real flood rows
  - SMOTE (after undersampling)            -> synthetic positives, TRAIN ONLY
  - XGBoost scale_pos_weight               -> boosted trees with weighting

Selection metric: PR-AUC (average precision) on the 99:1 VALIDATION set.
The test set is NOT touched here.
"""
from pathlib import Path
import json
import time

import joblib
import numpy as np
import pandas as pd
from imblearn.over_sampling import RandomOverSampler, SMOTE
from imblearn.pipeline import Pipeline as ImbPipeline
from imblearn.under_sampling import RandomUnderSampler
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ROOT / "data" / "processed" / "splits"
MODELS = ROOT / "models"
MODELS.mkdir(exist_ok=True)
REPORTS = ROOT / "reports"
SEED = 42

FEATURES = [
    "rain_1d", "rain_3d", "rain_7d", "rain_14d", "rain_30d", "rain_max1d_7d",
    "rain_days10_7d", "rain_3d_over_30d", "rain_7d_anom", "rain_30d_anom",
    "gwettop", "gwetroot", "gwetprof", "gwettop_chg_3d", "gwettop_chg_7d",
    "gwetroot_chg_7d", "gwetprof_chg_7d", "gwetroot_anom",
    "gwettop_rel_q99", "gwetroot_rel_q99", "gwetprof_rel_q99",
    "gwt_q99", "gwr_q99", "gwp_q99",
    "prov_rain_3d", "prov_rain_7d", "prov_gwettop", "prov_gwetroot", "prov_gwetprof",
    "t2m", "t2m_max", "t2m_max_7d", "t2m_max_chg_7d", "rh2m",
    "doy_sin", "doy_cos", "area_km2", "hist_floods_train", "is_flash_mode",
]
TARGET = "flood_next_7d"


def rf(max_samples=0.15, **kw):
    return RandomForestClassifier(n_estimators=150, max_depth=14, min_samples_leaf=10,
                                  max_features="sqrt", max_samples=max_samples, n_jobs=-1,
                                  random_state=SEED, **kw)


def build_models(pos_weight: float):
    xgb_kw = dict(n_estimators=300, learning_rate=0.05, max_depth=5, subsample=0.8,
                  colsample_bytree=0.8, min_child_weight=5, eval_metric="aucpr",
                  tree_method="hist", n_jobs=-1, random_state=SEED)
    return {
        "logreg_balanced": make_pipeline(StandardScaler(),
                                         LogisticRegression(class_weight="balanced", max_iter=1000)),
        "rf_plain": rf(),
        "rf_class_weight": rf(class_weight="balanced_subsample"),
        "rf_undersample": ImbPipeline([("us", RandomUnderSampler(sampling_strategy=0.1, random_state=SEED)),
                                       ("rf", rf(max_samples=None))]),
        "rf_oversample": ImbPipeline([("os", RandomOverSampler(sampling_strategy=0.1, random_state=SEED)),
                                      ("rf", rf())]),
        "rf_smote": ImbPipeline([("us", RandomUnderSampler(sampling_strategy=0.05, random_state=SEED)),
                                 ("sm", SMOTE(sampling_strategy=0.2, random_state=SEED)),
                                 ("rf", rf(max_samples=None))]),
        "xgb_plain": XGBClassifier(**xgb_kw),
        "xgb_weighted": XGBClassifier(scale_pos_weight=pos_weight, **xgb_kw),
    }


def best_f1_threshold(y, p):
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-12, None)
    i = int(np.nanargmax(f1[:-1]))
    return float(thr[i]), float(f1[i]), float(prec[i]), float(rec[i])


def main():
    tr = pd.read_parquet(SPLITS / "train.parquet").dropna(subset=FEATURES)
    va = pd.read_parquet(SPLITS / "val_99to1.parquet").dropna(subset=FEATURES)
    va_nat = pd.read_parquet(SPLITS / "val.parquet").dropna(subset=FEATURES)
    Xtr, ytr = tr[FEATURES], tr[TARGET]
    pos_w = float((ytr == 0).sum() / (ytr == 1).sum())
    print(f"Train rows {len(tr):,} (pos {int(ytr.sum()):,}, ratio {pos_w:.0f}:1) | "
          f"Val99 rows {len(va):,} (pos {int(va[TARGET].sum())})")

    rows, fitted = [], {}
    # Rule-based fallback from the project plan (UC04 3a): rainfall anomaly score
    for name, col in (("rule_rain_7d_anom", "rain_7d_anom"), ("rule_rain_7d", "rain_7d")):
        rows.append({"model": name, "val99_pr_auc": average_precision_score(va[TARGET], va[col]),
                     "val_natural_pr_auc": average_precision_score(va_nat[TARGET], va_nat[col]),
                     "train_pr_auc": average_precision_score(ytr, tr[col]), "fit_seconds": 0})
        print(f"{name:20s} val99 PR-AUC {rows[-1]['val99_pr_auc']:.3f}")

    for name, m in build_models(pos_w).items():
        t0 = time.time()
        m.fit(Xtr, ytr)
        p_va = m.predict_proba(va[FEATURES])[:, 1]
        p_nat = m.predict_proba(va_nat[FEATURES])[:, 1]
        p_tr = m.predict_proba(Xtr)[:, 1]
        thr, f1, pr, rc = best_f1_threshold(va[TARGET], p_va)
        rows.append({"model": name,
                     "val99_pr_auc": average_precision_score(va[TARGET], p_va),
                     "val_natural_pr_auc": average_precision_score(va_nat[TARGET], p_nat),
                     "train_pr_auc": average_precision_score(ytr, p_tr),
                     "val99_best_f1": f1, "val99_prec_at_thr": pr, "val99_rec_at_thr": rc,
                     "threshold": thr, "val99_pred_pos_rate_at_0.5": float((p_va >= 0.5).mean()),
                     "fit_seconds": round(time.time() - t0, 1)})
        fitted[name] = m
        print(f"{name:20s} val99 PR-AUC {rows[-1]['val99_pr_auc']:.3f} | "
              f"train {rows[-1]['train_pr_auc']:.3f} | F1 {f1:.3f} | {rows[-1]['fit_seconds']}s",
              flush=True)

    res = pd.DataFrame(rows).sort_values("val99_pr_auc", ascending=False)
    res.to_csv(REPORTS / "model_comparison_validation.csv", index=False)
    best = res[res.model.isin(fitted)].iloc[0]
    print("\n" + res.round(3).to_string(index=False))
    print(f"\nSelected (by validation PR-AUC): {best.model}")

    joblib.dump({"model": fitted[best.model], "features": FEATURES, "name": best.model,
                 "threshold": float(best.threshold), "train_years": "2000-2014",
                 "val_years": "2015-2018", "val99_pr_auc": float(best.val99_pr_auc),
                 "label_source": "DFO Global Flood Records (Zenodo 19288170)",
                 "target": "flood in district within next 7 days (not already flooded)"},
                MODELS / "flood_model.joblib")
    (REPORTS / "selected_model.json").write_text(json.dumps(
        {"selected": best.model, "threshold": float(best.threshold),
         "val99_pr_auc": float(best.val99_pr_auc)}, indent=2))


if __name__ == "__main__":
    main()
