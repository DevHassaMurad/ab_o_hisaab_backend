import json
from datetime import date
from pathlib import Path

from app.integrations.nasa_power import PowerDay, parse_power_json
from app.services.ingestion.staleness import StaleRow, count_not_due, plan_copy_forward
from app.services.ingestion.validation import validate_power_day

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nasa_power"


def day(**v):
    base = {"PRECTOTCORR": 1.0, "T2M": 30.0, "GWETROOT": 0.4, "GWETTOP": 0.5}
    base.update(v)
    return PowerDay(date(2022, 8, 20), base)


def test_clean_day_has_no_flags():
    v = validate_power_day(day())
    assert v.is_fresh and v.flags == [] and v.rainfall_mm == 1.0


def test_small_negative_rain_clamped():
    v = validate_power_day(day(PRECTOTCORR=-0.2))
    assert v.rainfall_mm == 0.0 and "rain_neg_clamped" in v.flags


def test_absurd_rain_rejected_makes_day_not_fresh():
    v = validate_power_day(day(PRECTOTCORR=2000.0))
    assert v.rainfall_mm is None and not v.is_fresh
    assert any(f.startswith("prectotcorr_out_of_range") for f in v.flags)


def test_soil_percent_converted_to_fraction():
    v = validate_power_day(day(GWETROOT=45.0))
    assert v.soil_wetness == 0.45 and "gwetroot_pct_to_fraction" in v.flags


def test_temperature_out_of_range_rejected_but_day_still_fresh():
    v = validate_power_day(day(T2M=70.0))
    assert v.temp_c is None and v.is_fresh


def test_fixture_with_fixes():
    days = {d.obs_date: validate_power_day(d) for d in parse_power_json(
        json.loads((FIX / "pct_soil.json").read_text())).days}
    assert days[date(2022, 8, 20)].soil_wetness == 0.45
    assert days[date(2022, 8, 21)].rainfall_mm == 0.0
    assert days[date(2022, 8, 22)].rainfall_mm is None


WINDOW = (date(2022, 8, 21), date(2022, 8, 30))


def test_copy_forward_only_overdue_days_and_capped():
    rows = plan_copy_forward(fresh_dates=set(), last_valid=date(2022, 8, 22), window=WINDOW,
                             today=date(2022, 8, 31), expected_lag=3, max_days=3)
    assert rows == [StaleRow(date(2022, 8, 23), date(2022, 8, 22)), StaleRow(date(2022, 8, 24), date(2022, 8, 22)),
                    StaleRow(date(2022, 8, 25), date(2022, 8, 22))]


def test_not_yet_due_days_are_not_stale():
    rows = plan_copy_forward(fresh_dates=set(), last_valid=date(2022, 8, 27), window=WINDOW,
                             today=date(2022, 8, 31), expected_lag=3, max_days=3)
    assert rows == [StaleRow(date(2022, 8, 28), date(2022, 8, 27))]


def test_no_previous_valid_row_means_no_copy():
    assert plan_copy_forward(fresh_dates=set(), last_valid=None, window=WINDOW, today=date(2022, 8, 31),
                             expected_lag=3, max_days=3) == []


def test_fresh_days_are_never_copied():
    rows = plan_copy_forward(fresh_dates={date(2022, 8, 23)}, last_valid=date(2022, 8, 22), window=WINDOW,
                             today=date(2022, 8, 31), expected_lag=3, max_days=3)
    assert date(2022, 8, 23) not in {r.obs_date for r in rows}


def test_count_not_due():
    assert count_not_due(date(2022, 8, 21), date(2022, 8, 30), date(2022, 8, 31), 3, set()) == 2
