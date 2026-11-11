"""Stage 7: one-time evaluation of the SELECTED model on the untouched 99:1 test set.

Reports PR-AUC, precision, recall, F1, confusion matrix (threshold fixed on
validation), collapse checks, event-level detection, and a PR curve plot.
"""
from pathlib import Path
import json

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (auc, average_precision_score, confusion_matrix, f1_score,
                             precision_recall_curve, precision_score, recall_score)

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ROOT / "data" / "processed" / "splits"
REPORTS = ROOT / "reports"
TARGET = "flood_next_7d"


def main():
    art = joblib.load(ROOT / "models" / "flood_model.joblib")
    model, feats, thr = art["model"], art["features"], art["threshold"]
    te = pd.read_parquet(SPLITS / "test_99to1.parquet")
    n_before = len(te)
    te = te.dropna(subset=feats)
    y = te[TARGET].values
    p = model.predict_proba(te[feats])[:, 1]
    yhat = (p >= thr).astype(int)

    prec_c, rec_c, _ = precision_recall_curve(y, p)
    tn, fp, fn, tp = confusion_matrix(y, yhat, labels=[0, 1]).ravel()
    res = {
        "model": art["name"], "threshold_from_validation": thr,
        "test_rows": int(len(te)), "dropped_rows_missing_features": int(n_before - len(te)),
        "test_positives": int(y.sum()), "test_negatives": int((y == 0).sum()),
        "test_ratio": f"{(y == 0).sum() / y.sum():.1f}:1",
        "pr_auc_average_precision": float(average_precision_score(y, p)),
        "pr_auc_trapezoid": float(auc(rec_c, prec_c)),
        "no_skill_pr_auc_baseline": float(y.mean()),
        "precision": float(precision_score(y, yhat, zero_division=0)),
        "recall": float(recall_score(y, yhat, zero_division=0)),
        "f1": float(f1_score(y, yhat, zero_division=0)),
        "confusion_matrix": {"TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp)},
        # Majority-class collapse checks
        "predicted_positive_rate": float(yhat.mean()),
        "actual_positive_rate": float(y.mean()),
        "prob_std": float(p.std()),
        "mean_prob_positives": float(p[y == 1].mean()),
        "mean_prob_negatives": float(p[y == 0].mean()),
        "all_negative_predictor": {"accuracy": float(1 - y.mean()), "recall": 0.0, "f1": 0.0},
    }
    # Event-level detection: was each test flood event flagged at least once in advance?
    pos = te[te[TARGET] == 1].assign(p=p[y == 1], hit=yhat[y == 1])
    ev = pos.groupby("event_id").agg(rows=("hit", "size"), flagged=("hit", "sum"),
                                     districts=("district_pcode", "nunique"),
                                     first_date=("date", "min"))
    res["events_in_test"] = int(len(ev))
    res["events_detected_at_least_once"] = int((ev.flagged > 0).sum())
    ev.to_csv(REPORTS / "test_event_detection.csv")
    # District-event level: share of (event, district) pairs flagged at least once
    de = pos.groupby(["event_id", "district_pcode"]).hit.max()
    res["district_events_detected"] = f"{int(de.sum())}/{len(de)}"
    # Detection by lead time
    res["recall_by_days_to_flood"] = {int(k): round(float(v), 3) for k, v in
                                      pos.groupby("days_to_flood").hit.mean().items()}
    res["pr_auc_by_flood_mode"] = {
        m: float(average_precision_score(y[te.flood_mode.values == m], p[te.flood_mode.values == m]))
        for m in ("slow", "flash") if y[te.flood_mode.values == m].sum() > 0}

    (REPORTS / "test_results.json").write_text(json.dumps(res, indent=2))

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
    ax[0].plot(rec_c, prec_c, lw=2, label=f"{art['name']} (PR-AUC={res['pr_auc_average_precision']:.3f})")
    ax[0].axhline(y.mean(), ls="--", c="gray", label=f"No-skill ({y.mean():.3f})")
    ax[0].axhline(0.70, ls=":", c="red", alpha=0)  # spacing
    ax[0].set(xlabel="Recall", ylabel="Precision", title="Test set (99:1) Precision-Recall")
    ax[0].legend(loc="upper right")
    cm = np.array([[tn, fp], [fn, tp]])
    ax[1].imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax[1].text(j, i, f"{v:,}", ha="center", va="center", fontsize=14,
                   color="white" if v > cm.max() / 2 else "black")
    ax[1].set(xticks=[0, 1], yticks=[0, 1], xticklabels=["Pred: No flood", "Pred: Flood"],
              yticklabels=["Actual: No flood", "Actual: Flood"],
              title=f"Confusion matrix (thr={thr:.3f} from validation)")
    plt.tight_layout()
    plt.savefig(REPORTS / "test_pr_curve_confusion.png", dpi=130)

    print(json.dumps(res, indent=2))
    verdict = "PASS" if res["pr_auc_average_precision"] >= 0.70 else "BELOW TARGET"
    print(f"\nC02 target PR-AUC >= 0.70: {verdict} ({res['pr_auc_average_precision']:.3f})")


if __name__ == "__main__":
    main()
