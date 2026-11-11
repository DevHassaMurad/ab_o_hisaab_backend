import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.integrations.nasa_power import (
    HttpNasaPowerClient,
    SourceUnavailable,
    UnitMismatch,
    check_units,
    parse_power_json,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nasa_power"


def load(name):
    return json.loads((FIX / name).read_text())


def test_parses_real_response_dates_and_values():
    r = parse_power_json(load("ok_7d.json"))
    assert r.days[0].obs_date == date(2022, 8, 20)
    assert len(r.days) == 7
    by_day = {d.obs_date: d.values for d in r.days}
    assert by_day[date(2022, 8, 24)]["PRECTOTCORR"] == pytest.approx(126.78)
    assert r.units == {"PRECTOTCORR": "mm/day", "T2M": "C", "GWETROOT": "1", "GWETTOP": "1"}


def test_fill_value_is_none():
    r = parse_power_json(load("fill_values.json"))
    by_day = {d.obs_date: d.values for d in r.days}
    assert by_day[date(2022, 8, 25)]["PRECTOTCORR"] is None
    assert by_day[date(2022, 8, 26)]["GWETROOT"] is None
    assert by_day[date(2022, 8, 26)]["PRECTOTCORR"] is not None


def test_ignores_non_daily_keys():
    doc = load("ok_7d.json")
    doc["properties"]["parameter"]["PRECTOTCORR"]["202208"] = 300.0  # month total key
    assert len(parse_power_json(doc).days) == 7


def test_missing_properties_is_source_unavailable():
    with pytest.raises(SourceUnavailable):
        parse_power_json({"messages": ["Request failed"], "header": {}})


def test_unit_mismatch_raises():
    with pytest.raises(UnitMismatch):
        check_units(parse_power_json(load("bad_units.json")).units)


def _client(handler, retries=3):
    s = Settings(POWER_MAX_RETRIES=retries, POWER_RETRY_BASE_S=0)
    return HttpNasaPowerClient(httpx.Client(transport=httpx.MockTransport(handler)), s, sleep=lambda _: None)


def test_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=load("ok_7d.json"))

    r = _client(handler).fetch_daily(26.8, 67.4, date(2022, 8, 20), date(2022, 8, 26))
    assert calls["n"] == 3 and len(r.days) == 7


def test_gives_up_after_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503)

    with pytest.raises(SourceUnavailable, match="after 3 retries"):
        _client(handler).fetch_daily(26.8, 67.4, date(2022, 8, 20), date(2022, 8, 26))
    assert calls["n"] == 4


def test_sends_expected_query():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=load("ok_7d.json"))

    _client(handler).fetch_daily(26.8, 67.4, date(2022, 8, 20), date(2022, 8, 26))
    assert seen["parameters"] == "PRECTOTCORR,T2M,GWETROOT,GWETTOP"
    assert seen["start"] == "20220820" and seen["end"] == "20220826"
    assert seen["community"] == "AG" and seen["time-standard"] == "UTC"
