"""Stage 4: time-based train / validation / test split + 99:1 evaluation sets.

- Split by YEAR so no flood event can appear in both training and testing.
- Train 2000-2014 | Validation 2015-2018 | Test 2019-2023
- Embargo: drop the last HORIZON days of each split (label window would peek
  into the next split).
- 99:1 sets: keep ALL real positives, randomly sample REAL negatives from the
  same years (fixed seed). No synthetic rows, no label changes.
  If there are not enough negatives, real positives are randomly subsampled
  instead (reported explicitly).
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
SPLITS = PROC / "splits"
SPLITS.mkdir(parents=True, exist_ok=True)
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

YEARS = {"train": (2000, 2014), "val": (2015, 2018), "test": (2019, 2023)}
RATIO = 99
SEED = 42
HORIZON = 7


def make_ratio_set(df: pd.DataFrame, ratio: int, seed: int):
    pos = df[df.flood_next_7d == 1]
    neg = df[df.flood_next_7d == 0]
    if len(neg) >= ratio * len(pos):
        neg_s = neg.sample(n=ratio * len(pos), random_state=seed)
        pos_s, note = pos, "all positives kept; negatives randomly sampled"
    else:
        n_pos = len(neg) // ratio
        pos_s = pos.sample(n=n_pos, random_state=seed)
        neg_s, note = neg.iloc[: n_pos * ratio], "all negatives kept; positives randomly sampled"
    out = pd.concat([pos_s, neg_s]).sample(frac=1, random_state=seed).reset_index(drop=True)
    return out, note


def main():
    df = pd.read_parquet(PROC / "dataset.parquet")
    summary = {}
    for name, (y0, y1) in YEARS.items():
        part = df[(df.year >= y0) & (df.year <= y1)]
        cutoff = pd.Timestamp(f"{y1}-12-31") - pd.Timedelta(days=HORIZON)
        part = part[part.date <= cutoff]
        part.to_parquet(SPLITS / f"{name}.parquet", index=False)
        s = {"years": f"{y0}-{y1}", "rows": len(part),
             "positives": int(part.flood_next_7d.sum()),
             "negatives": int((part.flood_next_7d == 0).sum()),
             "positive_events": int(part.loc[part.flood_next_7d == 1, "event_id"].nunique())}
        s["natural_ratio"] = round(s["negatives"] / max(s["positives"], 1), 1)
        if name in ("val", "test"):
            r, note = make_ratio_set(part, RATIO, SEED)
            r.to_parquet(SPLITS / f"{name}_99to1.parquet", index=False)
            p = int(r.flood_next_7d.sum()); n = len(r) - p
            s["set_99to1"] = {"rows": len(r), "positives": p, "negatives": n,
                              "ratio": f"{n / p:.1f}:1", "method": note,
                              "positive_events": int(r.loc[r.flood_next_7d == 1, "event_id"].nunique())}
        summary[name] = s

    # Leakage check: no event id shared across splits
    ev = {k: set(pd.read_parquet(SPLITS / f"{k}.parquet").query("event_id >= 0").event_id)
          for k in YEARS}
    summary["shared_events_train_test"] = sorted(ev["train"] & ev["test"])
    summary["shared_events_train_val"] = sorted(ev["train"] & ev["val"])
    summary["shared_events_val_test"] = sorted(ev["val"] & ev["test"])

    (REPORTS / "splits_summary.json").write_text(json.dumps(summary, indent=2, default=int))
    print(json.dumps(summary, indent=2, default=int))


if __name__ == "__main__":
    main()
