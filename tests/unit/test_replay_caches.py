"""Replay readers return only data available on the simulated day (no lookahead)."""
import json
from datetime import date

import pytest

from app.integrations.nasa_power import CachedNasaPowerClient, SourceUnavailable
from app.services.ingestion.imerg_ingestor import CachedImergSource


def test_committed_nasa_cache_has_no_lookahead():
    c = CachedNasaPowerClient("sindh-2022", replay_today=date(2022, 8, 15), lag_days=2)
    r = c.fetch_for("PK-SD-DAD", date(2022, 7, 1), date(2022, 9, 10))
    assert max(d.obs_date for d in r.days) == date(2022, 8, 13)
    assert min(d.obs_date for d in r.days) == date(2022, 7, 2)  # 30 days spin-up before 1 Aug


def test_sindh_2022_was_far_wetter_than_control_2021():
    """Sanity check on the real cached data: the flood year should dwarf the control year at Dadu."""
    def total(key):
        r = CachedNasaPowerClient(key, date(2030, 1, 1), 0).fetch_for("PK-SD-DAD", date(2000, 1, 1),
                                                                       date(2030, 1, 1))
        return sum(d.values["PRECTOTCORR"] or 0 for d in r.days)
    assert total("sindh-2022") > 3 * total("control-2021")


def test_outage_window_raises(tmp_path):
    d = tmp_path / "k" / "nasa_power"
    d.mkdir(parents=True)
    (d / "outages.json").write_text(json.dumps({"outages": [["2022-08-20", "2022-08-22"]]}))
    c = CachedNasaPowerClient("k", replay_today=date(2022, 8, 21), lag_days=2, root=tmp_path)
    with pytest.raises(SourceUnavailable):
        c.fetch_for("X", date(2022, 8, 1), date(2022, 8, 20))


def test_imerg_cache_cutoff(tmp_path):
    (tmp_path / "k").mkdir()
    (tmp_path / "k" / "imerg_daily.csv").write_text(
        "date,tehsil_pcode,rainfall_mm,rainfall_max_3h_mm,n_images\n"
        "2022-08-13,T1,10.5,4.0,48\n2022-08-14,T1,30.0,12.0,48\n")
    s = CachedImergSource("k", replay_today=date(2022, 8, 14), lag_days=1, root=tmp_path)
    assert s.image_count(date(2022, 8, 13)) == 48
    assert s.image_count(date(2022, 8, 14)) == 0  # not available yet on the simulated day
