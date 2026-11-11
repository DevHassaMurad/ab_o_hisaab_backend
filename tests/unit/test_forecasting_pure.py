"""FR3/FR4 pure logic: rounding, tiers, peaks, aggregation, fallback rule (spec sanity examples), mode spec
validation, Open-Meteo daily maths, explanation numbers, threshold report."""
import re
import uuid
from datetime import date

import pytest
import yaml

from app.integrations.forecast_rain import GeoPoint, daily_from_hourly, records_for
from app.services.explain.template_explainer import RiskExplainInputs, TemplateExplainService
from app.services.forecasting.modes import ModeRegistry, ModeSpec
from app.services.forecasting.scoring import (
    aggregate_district,
    fallback_score,
    peak,
    round_half_up_score,
    tier_for,
    top_factors,
)
from ml.threshold_report import enforce_increasing, threshold_report

SLOW, FLASH = ModeRegistry.get("SLOW_ONSET"), ModeRegistry.get("FLASH")
T = {"WATCH": 40, "WARNING": 60, "ACTION": 80}


def test_round_half_up_not_bankers():
    assert round_half_up_score(0.405) == 41
    assert round_half_up_score(0.5) == 50 and round_half_up_score(1.2) == 100


@pytest.mark.parametrize("score,tier", [(39, "NONE"), (40, "WATCH"), (63, "WARNING"), (80, "ACTION")])
def test_tiers(score, tier):
    assert tier_for(score, T) == tier


def test_custom_thresholds():
    assert tier_for(55, {"WATCH": 30, "WARNING": 50, "ACTION": 70}) == "WARNING"


def test_peak_earliest_on_ties():
    assert peak({1: 0.3, 2: 0.7, 5: 0.7}) == (2, 0.7)


def test_aggregation_ignores_small_population_tehsil():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [(a, 1, 0.5), (b, 1, 0.6), (c, 1, 0.95)]
    agg = aggregate_district(rows, {a: 600, b: 320, c: 80}, 0.10)
    assert agg.p_by_lead[1] == 0.6 and agg.source_by_lead[1] == b and c not in agg.eligible


def test_aggregation_without_population_uses_all():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert aggregate_district([(a, 1, 0.2), (b, 1, 0.7)], {a: None, b: None}, 0.10).p_by_lead[1] == 0.7


def test_slow_fallback_matches_spec_examples():
    normal = {"rain_7d_mm": 20, "rain_30d_mm": 60, "soil_wetness_root": 0.35, "vulnerability_weight": 0.6}
    assert round_half_up_score(fallback_score(normal, SLOW)[0]) == 24
    flood = {"rain_7d_mm": 200, "rain_30d_mm": 400, "soil_wetness_root": 0.9, "vulnerability_weight": 0.8}
    assert round_half_up_score(fallback_score(flood, SLOW)[0]) == 96


def test_flash_fallback_and_lead_weights_match_spec_example():
    buner = {"max_3h_fcst_mm": 80, "max_3h_obs_mm": 0, "rain_48h_fcst_mm": 130, "slope_mean_deg": 28,
             "soil_wetness_top": 0.7, "vulnerability_weight": 0.7}
    p, _ = fallback_score(buner, FLASH)
    assert round_half_up_score(p * FLASH.lead_weights[2]) == 93
    assert round_half_up_score(p * FLASH.lead_weights[6]) == 56  # same burst 6 days out: only Watch


def test_flash_cannot_exceed_60_from_leads_5_to_7():
    assert all(FLASH.lead_weights[d] <= 0.6 for d in (5, 6, 7))


def test_top_factors_shares():
    f = top_factors({"rain_7d_mm": 0.3, "soil_wetness_root": 0.1, "lead_day": 0.5}, {"rain_7d_mm": 184,
                                                                                     "soil_wetness_root": 0.91})
    assert [x["factor"] for x in f] == ["rain_7d_mm", "soil_wetness_root"] and f[0]["contribution"] == 0.75


def _raw(mode="slow_onset"):
    return yaml.safe_load(open(f"ml/config/modes/{mode}.yaml"))


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r["fallback"]["terms"][0].update(weight=0.5), "sum to 1"),
    (lambda r: r["features"].append("nonsense"), "unknown features"),
    (lambda r: r["features"].remove("rain_3d_mm"), "core features"),
    (lambda r: r["lead_weights"].update({7: 1.5}), "lead_weights"),
])
def test_invalid_mode_definitions_rejected(mutate, msg):
    raw = _raw()
    mutate(raw)
    with pytest.raises(ValueError, match=msg):
        ModeSpec.model_validate(raw)


def test_flash_weights_must_not_increase():
    raw = _raw("flash")
    raw["lead_weights"][7] = 1.0
    with pytest.raises(ValueError, match="non-increasing"):
        ModeSpec.model_validate(raw)


def test_daily_from_hourly_total_and_3h_burst():
    times = [f"2025-08-15T{h:02d}:00" for h in range(24)]
    precip = [0.0] * 24
    precip[22], precip[23] = 30.0, 25.0
    d = daily_from_hourly(times, precip)[date(2025, 8, 15)]
    assert d == (55.0, 55.0)


def test_records_for_short_forecast_uses_persistence_flagged_stale():
    p = GeoPoint(uuid.uuid4(), uuid.uuid4(), 34.5, 72.5)
    daily = {date(2025, 8, 10 + i): (float(i), float(i)) for i in range(6)}  # issue 10 Aug, leads 1..5 only
    recs = records_for(p, daily, date(2025, 8, 10), "LIVE_FORECAST", "m")
    leads = {r.lead_day: r for r in recs if r.lead_day}
    assert sorted(leads) == [1, 2, 3, 4, 5, 6, 7]
    assert leads[6].rainfall_mm == leads[5].rainfall_mm and leads[6].is_stale and not leads[5].is_stale


def test_explanation_numbers_come_from_inputs():
    i = RiskExplainInputs("Dadu", 82, "ACTION", 2, date(2022, 8, 24),
                          [{"factor": "rain_7d_mm", "value": 184.0, "contribution": 0.6},
                           {"factor": "soil_wetness_root", "value": 0.91, "contribution": 0.4}],
                          False, False, None, False)
    en, _ = TemplateExplainService().explain_risk(i)
    assert "82/100 (Action)" in en and "184 mm" in en and "91%" in en and "2 day" in en
    allowed = {"82", "100", "2", "24", "184", "91", "3", "7"}
    assert set(re.findall(r"\d+", en)) <= allowed


def test_fallback_notice():
    i = RiskExplainInputs("Dadu", 50, "WATCH", 1, date(2022, 8, 24), [], True, True, date(2022, 8, 20), True)
    en, _ = TemplateExplainService().explain_risk(i)
    assert "rainfall rule" in en and "20 Aug" in en and "proxy" in en


def test_threshold_report_and_increasing_suggestions():
    d0 = date(2020, 8, 20)
    oof = [{"district_id": "D", "run_date": date(2020, 8, d), "lead_day": 1, "p_cal": p}
           for d, p in [(10, 0.2), (15, 0.5), (18, 0.85), (19, 0.9), (30, 0.45)]]
    rep = threshold_report(oof, [{"district_id": "D", "onset_date": d0}], {i: 1.0 for i in range(1, 8)},
                           {"WATCH": 3.0, "WARNING": 1.5, "ACTION": 0.5}, "SLOW_ONSET")
    row40 = next(r for r in rep["thresholds"] if r["threshold"] == 40)
    assert row40["hit_rate"] == 1.0 and row40["median_lead_days"] == 5
    assert row40["false_alarm_days_per_season"] == 1.0  # the 30 Aug day is > 7 days after onset
    assert rep["advisory"] is True
    assert enforce_increasing({"WATCH": 40, "WARNING": 40, "ACTION": 45}) == {"WATCH": 40, "WARNING": 45,
                                                                               "ACTION": 50}
