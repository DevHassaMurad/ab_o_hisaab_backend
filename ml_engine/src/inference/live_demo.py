r"""Live Demo Runner & 5 Chronological Real Test Data Splits for Aab-o-Hisaab AI.

Removes the old batch files and splits the unseen 2019–2025 real dataset strictly by
TIME into 5 chronological 99:1 test files (`5,000` rows each = `50` actual flood days +
`4,950` normal days = exact `99:1` ratio), WITHOUT touching or filtering by the model's
predictions (`prob` or `risk_score`).

The 5 Time-Split Files in `data/demo_test_files/`:
  1. `time_split_1_2019_2020.csv`      (2019-01-01 to 2020-12-31 — 5,000 rows, 99:1)
  2. `time_split_2_2021_2022_h1.csv`   (2021-01-01 to 2022-06-30 — 5,000 rows, 99:1)
  3. `time_split_3_2022_h2_2023.csv`   (2022-07-01 to 2023-12-31 — 5,000 rows, 99:1)
  4. `time_split_4_2024_full_year.csv` (2024-01-01 to 2024-12-31 — 5,000 rows, 99:1)
  5. `time_split_5_2025_full_year.csv` (2025-01-01 to 2025-12-24 — 5,000 rows, 99:1)

Usage:
  - Generate & evaluate all 5 chronological test files:
      .\.venv\Scripts\python.exe src\inference\live_demo.py
  - Test a specific file live:
      .\.venv\Scripts\python.exe src\inference\live_demo.py --file data\demo_test_files\time_split_1_2019_2020.csv
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from src.inference.flood_model import FloodForecastModel, RiskEngine

PROC = ROOT / "data" / "processed"
DEMO_DIR = ROOT / "data" / "demo_test_files"


def load_real_pool(feats: list[str]) -> pd.DataFrame:
    ds = pd.read_parquet(PROC / "dataset.parquet").dropna(subset=feats)
    inf = pd.read_parquet(PROC / "inference_2024_2025.parquet").dropna(subset=feats)
    full = pd.concat([ds, inf], ignore_index=True).drop_duplicates(subset=["district_pcode", "date"])
    full["date_str"] = full["date"].astype(str).str[:10]
    return full


def generate_5_time_split_files(full_df: pd.DataFrame, feats: list[str]) -> list[tuple[Path, str]]:
    if DEMO_DIR.exists():
        for old_f in DEMO_DIR.glob("*.csv"):
            old_f.unlink()
    else:
        DEMO_DIR.mkdir(parents=True, exist_ok=True)

    meta_cols = ["district_pcode", "district_name", "province", "date_str", "flood_mode", "year"]
    raw_cols = list(dict.fromkeys(meta_cols + feats + ["flood_next_7d"]))

    time_windows = [
        ("untested_split_1_2019.csv", "2019-01-01", "2019-12-31", 50, 301, "Time Split 1: Full Year 2019 (Unseen 99:1 Split)"),
        ("untested_split_2_2020.csv", "2020-01-01", "2020-12-31", 50, 302, "Time Split 2: Full Year 2020 (Unseen 99:1 Split)"),
        ("untested_split_3_2021_2022.csv", "2021-01-01", "2022-12-31", 50, 303, "Time Split 3: Years 2021–2022 (Unseen 99:1 Split)"),
        ("untested_split_4_2023_2024.csv", "2023-01-01", "2024-12-31", 50, 304, "Time Split 4: Years 2023–2024 (Unseen 99:1 Split)"),
        ("untested_split_5_2025.csv", "2025-01-01", "2025-12-31", 50, 305, "Time Split 5: Full Year 2025 (Unseen 99:1 Split)"),
    ]

    saved = []
    for fname, dt_start, dt_end, n_pos_target, seed, desc in time_windows:
        sub = full_df[(full_df["date_str"] >= dt_start) & (full_df["date_str"] <= dt_end)]
        pos_df = sub[sub["flood_next_7d"] == 1]
        neg_df = sub[sub["flood_next_7d"] == 0]

        n_p = min(n_pos_target, len(pos_df))
        n_n = n_p * 99

        # Pure random sampling by ground-truth label ONLY — sorted chronologically by date!
        s_pos = pos_df.sample(n=n_p, random_state=seed)
        s_neg = neg_df.sample(n=n_n, random_state=seed)
        df_out = (
            pd.concat([s_pos, s_neg], ignore_index=True)
            .sort_values(["date_str", "district_name"], ascending=[True, True])
            .reset_index(drop=True)[raw_cols]
        )

        fpath = DEMO_DIR / fname
        df_out.to_csv(fpath, index=False)
        saved.append((fpath, desc))
    return saved


def test_csv_file(csv_path: Path, fm: FloodForecastModel, engine: RiskEngine, show_rows: int = 10):
    df = pd.read_csv(csv_path)
    probs = fm.predict_proba(df)
    scores = engine.probability_to_score(probs)
    tiers = [engine.score_to_tier(s)["tier"] for s in scores]
    releases = [int(engine.score_to_tier(s)["release_pct"] * 100) for s in scores]

    df["pred_prob"] = probs.round(4)
    df["risk_score"] = scores
    df["tier"] = tiers
    df["release_pct"] = releases

    tier_counts = df["tier"].value_counts().to_dict()
    print("=" * 95)
    print(f"FILE: {csv_path.name}  |  Total Rows: {len(df):,}  |  Date Range: {df['date_str'].min()} to {df['date_str'].max()}")
    print(f"Predicted Tier Breakdown: {tier_counts}")
    if "flood_next_7d" in df.columns:
        y = df["flood_next_7d"].to_numpy(dtype=int)
        preds = (probs >= fm.threshold).astype(int)
        pos, neg = int(y.sum()), int((y == 0).sum())
        tn, fp, fn, tp = confusion_matrix(y, preds, labels=[0, 1]).ravel()
        pr_auc = average_precision_score(y, probs)
        rec = recall_score(y, preds, zero_division=0)
        prec = precision_score(y, preds, zero_division=0)
        f1 = f1_score(y, preds, zero_division=0)
        print(
            f"Honest 99:1 Metrics     : PR-AUC = {pr_auc:.4f} ({pr_auc*100:.1f}%)  |  "
            f"Recall = {rec*100:.1f}%  |  Precision = {prec*100:.1f}%  |  F1 = {f1:.4f}"
        )
        print(
            f"Confusion Matrix (99:1) : TP={tp} Caught, FN={fn} Missed, TN={tn} Correct Safe, FP={fp} False Alarms "
            f"(Ratio={neg//max(pos,1)}:1)"
        )
    print("-" * 95)
    cols = ["district_name", "province", "date_str", "flood_mode", "rain_7d", "rain_30d", "gwettop", "flood_next_7d", "risk_score", "tier"]
    # Show a preview ordered chronologically by date_str
    top_alerts = df.sort_values("risk_score", ascending=False).head(show_rows // 2)
    sample_other = df.head(show_rows // 2)
    preview = (
        pd.concat([top_alerts, sample_other])
        .drop_duplicates()
        .head(show_rows)
        .sort_values(["date_str", "district_name"], ascending=[True, True])
    )
    print(preview[cols].to_string(index=False))
    print("=" * 95 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Live Demo Runner & 5 Chronological 99:1 Test Splits")
    parser.add_argument("--file", type=str, default=None, help="Path to one of the 5 CSV test files to run")
    args = parser.parse_args()

    fm = FloodForecastModel()
    engine = RiskEngine()

    if args.file:
        fpath = Path(args.file)
        if not fpath.exists():
            fpath = ROOT / args.file
        test_csv_file(fpath, fm, engine, show_rows=12)
        return

    full_df = load_real_pool(fm.features)
    saved_files = generate_5_time_split_files(full_df, fm.features)
    print(f"Removed old batch files and created 5 honest chronological 99:1 test files (5,000 rows each) in:\n  {DEMO_DIR}\n")
    for fpath, desc in saved_files:
        print(f"--- {desc} ---")
        test_csv_file(fpath, fm, engine, show_rows=8)


if __name__ == "__main__":
    main()
