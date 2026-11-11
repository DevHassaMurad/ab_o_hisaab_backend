"""FR4 thresholds calibration report (advisory only; never writes TriggerTier).

Input: out-of-fold calibrated predictions aggregated to districts (written by training as oof records) and flood
events. For thresholds 30..90 step 5: hit rate (event caught within the 10 days before onset), median lead days,
false-alarm days per district-season (score >= t more than 7 days from any event). Suggestion per tier = smallest
threshold whose false-alarm rate fits the mode's budget, then forced to increase WATCH < WARNING < ACTION.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from typing import Any

THRESHOLDS = list(range(30, 95, 5))


def daily_scores(oof: list[dict[str, Any]], lead_weights: dict[int, float]) -> dict[tuple[str, date], int]:
    """oof rows: district_id, run_date (date), lead_day, p_cal. Score per district-day = max over leads."""
    out: dict[tuple[str, date], int] = {}
    for r in oof:
        s = max(0, min(100, round(100 * r["p_cal"] * lead_weights[int(r["lead_day"])])))
        k = (r["district_id"], r["run_date"])
        out[k] = max(out.get(k, 0), s)
    return out


def enforce_increasing(s: dict[str, int | None], step: int = 5, cap: int = 95) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    prev = None
    for tier in ("WATCH", "WARNING", "ACTION"):
        v = s.get(tier)
        if v is not None and prev is not None and v <= prev:
            v = prev + step
        if v is not None and v > cap:
            v = None
        out[tier] = v
        prev = v if v is not None else prev
    return out


def threshold_report(oof: list[dict[str, Any]], events: list[dict[str, Any]], lead_weights: dict[int, float],
                     budget: dict[str, float], mode: str) -> dict[str, Any]:
    daily = daily_scores(oof, lead_weights)
    by_district: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for (did, d), s in daily.items():
        by_district[did].append((d, s))
    seasons = len({(did, d.year) for did, d in daily}) or 1
    ev_by_d: dict[str, list[date]] = defaultdict(list)
    for e in events:
        ev_by_d[e["district_id"]].append(e["onset_date"])
    rows = []
    for t in THRESHOLDS:
        hits, leads = 0, []
        for e in events:
            pre = [d for d, s in by_district.get(e["district_id"], [])
                   if s >= t and 0 < (e["onset_date"] - d).days <= 10]
            if pre:
                hits += 1
                leads.append((e["onset_date"] - min(pre)).days)
        fa = sum(1 for (did, d), s in daily.items()
                 if s >= t and all(abs((d - o).days) > 7 for o in ev_by_d.get(did, [])))
        rows.append({"threshold": t, "hit_rate": round(hits / max(1, len(events)), 2),
                     "median_lead_days": int(statistics.median(leads)) if leads else None,
                     "false_alarm_days_per_season": round(fa / seasons, 2)})
    suggest = {tier: min((r["threshold"] for r in rows if r["false_alarm_days_per_season"] <= b), default=None)
               for tier, b in budget.items()}
    return {"mode": mode, "source": "out_of_fold", "seasons": seasons, "thresholds": rows,
            "suggested_thresholds": enforce_increasing(suggest), "false_alarm_budget": budget, "advisory": True}
