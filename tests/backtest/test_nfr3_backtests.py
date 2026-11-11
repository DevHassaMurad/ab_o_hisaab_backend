"""NFR3 acceptance: run each scenario start to end through the real pipeline and evaluate it. Slow, so excluded by
default: run with `pytest -m backtest`. Reports are written to reports/backtest/<key>.json."""
import json
from pathlib import Path

import pytest

from app.main import register_all
from app.services.replay.engine import ReplayEngine
from app.services.replay.scenarios import get_scenario_by_key
from app.services.triggers.fund_settings import seed_live_funds

pytestmark = pytest.mark.backtest
OUT = Path("reports/backtest")


@pytest.mark.parametrize("key", ["sindh-2022", "buner-2025", "control-2021"])
def test_nfr3(seeded, key):
    register_all()
    seed_live_funds(seeded)
    e = ReplayEngine()
    e.start(seeded, key)
    sc = get_scenario_by_key(seeded, key)
    while sc.status == "RUNNING":
        e.step(seeded, key, min(30, (sc.end_date - sc.sim_date).days + 1))
        sc = get_scenario_by_key(seeded, key)
    report = e.backtest(seeded, key)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    assert report["complete"]
    assert report["passed"], "\n".join(report["failures"])
