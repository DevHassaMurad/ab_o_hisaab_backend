"""Step 6 (Stage 3): validate the modelling dataset before any training.

Checks: missing values, duplicates, physically impossible values, label sanity,
class balance, coverage across provinces / years / months.
Writes reports/dataset_validation.json and prints a readable summary.
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

BOUNDS = {  # physically plausible ranges
    "rain_1d": (0, 500), "rain_7d": (0, 1500), "rain_30d": (0, 3000),
    "gwettop": (0, 1), "gwetroot": (0, 1), "gwetprof": (0, 1),
    "t2m": (-45, 55), "t2m_max": (-40, 60), "rh2m": (0, 100),
}


def main():
    df = pd.read_parquet(PROC / "dataset.parquet")
    rep = {"rows": len(df), "districts": int(df["district_pcode"].nunique()),
           "date_min": str(df["date"].min().date()), "date_max": str(df["date"].max().date())}

    # 1. Duplicates
    dups = int(df.duplicated(["district_pcode", "date"]).sum())
    rep["duplicate_district_dates"] = dups

    # 2. Missing values
    miss = df.isna().sum()
    rep["missing_values"] = {k: int(v) for k, v in miss[miss > 0].items()}

    # 3. Impossible values
    bad = {}
    for c, (lo, hi) in BOUNDS.items():
        n = int(((df[c] < lo) | (df[c] > hi)).sum())
        if n:
            bad[c] = n
    rep["out_of_range"] = bad

    # 4. Label sanity
    y = df["flood_next_7d"]
    rep["label_values"] = sorted(y.unique().tolist())
    rep["positives_without_event_id"] = int(((y == 1) & (df["event_id"] < 0)).sum())
    rep["negatives_with_event_id"] = int(((y == 0) & (df["event_id"] >= 0)).sum())
    pos, neg = int(y.sum()), int((y == 0).sum())
    rep["positives"], rep["negatives"] = pos, neg
    rep["natural_neg_to_pos_ratio"] = round(neg / pos, 1)
    rep["positive_events"] = int(df.loc[y == 1, "event_id"].nunique())

    # Do positives actually look wetter? (sanity, not proof)
    rep["median_rain_7d_pos_vs_neg"] = [round(float(df.loc[y == 1, "rain_7d"].median()), 1),
                                        round(float(df.loc[y == 0, "rain_7d"].median()), 1)]

    # 5. Coverage
    rep["by_province"] = (df.groupby("province")["flood_next_7d"]
                          .agg(["size", "sum"]).rename(columns={"size": "rows", "sum": "pos"})
                          .astype(int).to_dict(orient="index"))
    rep["by_year"] = (df.groupby("year")["flood_next_7d"].agg(["size", "sum"])
                      .rename(columns={"size": "rows", "sum": "pos"}).astype(int)
                      .to_dict(orient="index"))
    rep["pos_by_month"] = {int(k): int(v) for k, v in
                           df[y == 1].groupby("month").size().items()}

    (REPORTS / "dataset_validation.json").write_text(json.dumps(rep, indent=2, default=str))

    print(f"Rows {rep['rows']:,} | districts {rep['districts']} | "
          f"{rep['date_min']} -> {rep['date_max']}")
    print(f"Duplicates: {dups}")
    print(f"Missing values: {rep['missing_values'] or 'none'}")
    print(f"Out-of-range: {bad or 'none'}")
    print(f"Label values {rep['label_values']}; positives w/o event: "
          f"{rep['positives_without_event_id']}; negatives with event: {rep['negatives_with_event_id']}")
    print(f"Positives {pos:,} | Negatives {neg:,} | natural ratio {rep['natural_neg_to_pos_ratio']}:1 "
          f"| events {rep['positive_events']}")
    print(f"Median 7-day rain  pos vs neg: {rep['median_rain_7d_pos_vs_neg']} mm")
    print("\nBy province (rows / positives):")
    for k, v in rep["by_province"].items():
        print(f"  {k:20s} {v['rows']:>8,} {v['pos']:>6,}")
    print("\nPositives by year:", {k: v["pos"] for k, v in rep["by_year"].items()})
    print("Positives by month:", rep["pos_by_month"])


if __name__ == "__main__":
    main()
