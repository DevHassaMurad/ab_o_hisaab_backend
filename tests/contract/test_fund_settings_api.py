"""FR6: validation (all errors, nothing saved), audit (one per effective save, none on no-op), locking, approval
lock, copy draft, role matrix, scenario isolation, and the shared tier rule used by FR3."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.main import create_app
from app.models.core import AuditLog, ReplayScenario
from app.models.forecasting import RiskIndex
from app.models.funds import DisasterFund, TriggerTier
from app.models.geography import District
from app.services.triggers.fund_settings import seed_live_funds
from app.services.triggers.registry import register_fund_rules
from app.services.triggers.tiers import TierConfig, TierRule, tier_for_score


@pytest.fixture
def ctx(seeded):
    seed_live_funds(seeded)
    seeded.flush()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: seeded
    c = TestClient(app)

    def login(email):
        r = c.post("/api/v1/auth/token", data={"username": email, "password": "demo"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    d = {n: seeded.scalar(select(District).where(District.name == n)) for n in ("Dadu", "Buner", "Jacobabad",
                                                                               "Peshawar")}
    return c, login, d, seeded


def body(**over):
    b = {"version": 1, "allocated_pkr": "50000000.00",
         "tiers": [{"name": "WATCH", "min_score": 40, "release_pct": 10},
                   {"name": "WARNING", "min_score": 60, "release_pct": 30},
                   {"name": "ACTION", "min_score": 80, "release_pct": 60}],
         "household_share_pct": 70, "pool_spillover_enabled": True, "shopkeeper_cap_pkr": "50000.00",
         "shopkeeper_stock_pct": 40, "household_amount_by_tier": {"WATCH": "3000.00", "WARNING": "7000.00",
                                                                  "ACTION": "15000.00"},
         "event_close_days": 14}
    b.update(over)
    return b


def audits(db):
    return db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "FUND_SETTINGS_UPDATED"))


def test_get_shape(ctx):
    c, login, d, _ = ctx
    r = c.get(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo")).json()
    assert r["allocated_pkr"] == "50000000.00" and r["shopkeeper_share_pct"] == 30
    assert [t["cumulative_pct"] for t in r["tiers"]] == [10, 40, 100]
    assert [t["requires_approval"] for t in r["tiers"]] == [False, False, True]
    assert r["applies_from"] == "next_daily_run" and r["version"] == 1


def test_valid_save_bumps_version_and_audits_once(ctx):
    c, login, d, db = ctx
    tiers = body()["tiers"]
    tiers[1] = {"name": "WARNING", "min_score": 65, "release_pct": 30}
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(allocated_pkr="60000000.00", tiers=tiers))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2 and out["remaining_pkr"] == "60000000.00"
    assert set(out["changed_fields"]) == {"allocated_pkr", "tiers.WARNING.min_score"} and out["audit_seq"]
    a = db.scalar(select(AuditLog).where(AuditLog.event_type == "FUND_SETTINGS_UPDATED"))
    assert a.payload["before"]["allocated_pkr"] == "50000000.00" and a.payload["after"]["tiers"]["WARNING"] == [65, 30]
    assert audits(db) == 1


def test_noop_writes_nothing(ctx):
    c, login, d, db = ctx
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body())
    assert r.status_code == 200 and r.json()["changed_fields"] == [] and r.json()["audit_seq"] is None
    assert r.json()["version"] == 1 and audits(db) == 0


@pytest.mark.parametrize("over,code", [
    ({"tiers": [{"name": "WATCH", "min_score": 40, "release_pct": 10}, {"name": "WARNING", "min_score": 60,
      "release_pct": 30}, {"name": "ACTION", "min_score": 80, "release_pct": 50}]}, "TIER_PCT_SUM_INVALID"),
    ({"tiers": [{"name": "WATCH", "min_score": 40, "release_pct": 10}, {"name": "WARNING", "min_score": 60,
      "release_pct": 30}, {"name": "ACTION", "min_score": 60, "release_pct": 60}]}, "TIER_THRESHOLD_ORDER_INVALID"),
    ({"tiers": [{"name": "WATCH", "min_score": 0, "release_pct": 10}, {"name": "WARNING", "min_score": 60,
      "release_pct": 30}, {"name": "ACTION", "min_score": 101, "release_pct": 60}]}, "TIER_THRESHOLD_RANGE_INVALID"),
    ({"tiers": [{"name": "WATCH", "min_score": 40, "release_pct": 10}]}, "TIER_SET_INVALID"),
    ({"allocated_pkr": "-5"}, "FUND_AMOUNT_INVALID"),
    ({"allocated_pkr": "100.123"}, "FUND_AMOUNT_INVALID"),
    ({"household_share_pct": 120}, "SPLIT_PCT_INVALID"),
    ({"shopkeeper_cap_pkr": "-1"}, "SHOPKEEPER_CAP_INVALID"),
    ({"household_amount_by_tier": {"WATCH": "3000"}}, "HOUSEHOLD_AMOUNTS_INVALID"),
    ({"event_close_days": 0}, "EVENT_CLOSE_DAYS_INVALID"),
])
def test_invalid_saves_nothing(ctx, over, code):
    c, login, d, db = ctx
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(**over))
    assert r.status_code == 422 and code in [e["code"] for e in r.json()["errors"]]
    fund = db.scalar(select(DisasterFund).where(DisasterFund.district_id == d["Dadu"].id))
    assert fund.version == 1 and str(fund.allocated_pkr) == "50000000.00" and audits(db) == 0


def test_all_errors_returned_at_once(ctx):
    c, login, d, _ = ctx
    tiers = body()["tiers"]
    tiers[2] = {"name": "ACTION", "min_score": 80, "release_pct": 50}
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(tiers=tiers, shopkeeper_cap_pkr="-1"))
    codes = [e["code"] for e in r.json()["errors"]]
    assert codes == ["TIER_PCT_SUM_INVALID", "SHOPKEEPER_CAP_INVALID"] and r.json()["code"] == codes[0]


def test_below_committed_rejected(ctx):
    c, login, d, db = ctx
    fund = db.scalar(select(DisasterFund).where(DisasterFund.district_id == d["Dadu"].id))
    fund.reserved_pkr, fund.disbursed_pkr = 2_000_000, 3_000_000
    fund.remaining_pkr = fund.allocated_pkr - 5_000_000
    db.flush()
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(allocated_pkr="4000000.00"))
    assert r.json()["code"] == "FUND_BELOW_COMMITTED"


def test_requires_approval_locked(ctx):
    c, login, d, db = ctx
    tiers = body()["tiers"]
    tiers[2]["requires_approval"] = False
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(tiers=tiers))
    assert r.status_code == 422 and r.json()["code"] == "REQUIRES_APPROVAL_LOCKED"
    action = db.scalar(select(TriggerTier).join(DisasterFund).where(DisasterFund.district_id == d["Dadu"].id,
                                                                    TriggerTier.name == "ACTION"))
    assert action.requires_approval is True


def test_version_conflict(ctx):
    c, login, d, _ = ctx
    h = login("officer@pdma-sindh.demo")
    url = f"/api/v1/districts/{d['Dadu'].id}/fund-settings"
    assert c.put(url, headers=h, json=body(allocated_pkr="51000000.00")).status_code == 200
    r = c.put(url, headers=h, json=body(allocated_pkr="52000000.00"))  # stale version 1
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"


def test_first_save_creates_fund(ctx):
    c, login, d, db = ctx
    r = c.put(f"/api/v1/districts/{d['Peshawar'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
              json=body(version=0, allocated_pkr="10000000.00"))
    assert r.status_code == 200 and r.json()["version"] == 1
    assert db.scalar(select(AuditLog).where(AuditLog.event_type == "FUND_SETTINGS_UPDATED")).payload["created"]


def test_copy_draft(ctx):
    c, login, d, db = ctx
    h = login("officer@pdma-sindh.demo")
    tiers = body()["tiers"]
    tiers[0] = {"name": "WATCH", "min_score": 35, "release_pct": 10}
    c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=h,
          json=body(tiers=tiers, allocated_pkr="70000000.00"))
    n = audits(db)
    url = f"/api/v1/districts/{d['Jacobabad'].id}/fund-settings/draft"
    dr = c.get(url, params={"copy_from": str(d["Dadu"].id)}, headers=h).json()
    assert dr["tiers"][0]["min_score"] == 35 and dr["allocated_pkr"] == "50000000.00"
    assert dr["copied_from"]["district_name"] == "Dadu" and audits(db) == n
    dr2 = c.get(url, params={"copy_from": str(d["Dadu"].id), "include_amount": True}, headers=h).json()
    assert dr2["allocated_pkr"] == "70000000.00"
    same = c.get(url, params={"copy_from": str(d["Jacobabad"].id)}, headers=h)
    assert same.json()["code"] == "COPY_SOURCE_SAME_AS_TARGET"
    nofund = c.get(url, params={"copy_from": str(d["Peshawar"].id)}, headers=h)
    assert nofund.json()["code"] == "SOURCE_FUND_NOT_FOUND"


def test_role_matrix_and_list(ctx):
    c, login, d, _ = ctx
    dadu_url = f"/api/v1/districts/{d['Dadu'].id}/fund-settings"
    assert c.get(dadu_url, headers=login("dc.dadu@sindh.demo")).status_code == 200
    assert c.get(f"/api/v1/districts/{d['Buner'].id}/fund-settings",
                 headers=login("dc.dadu@sindh.demo")).status_code == 403
    for who in ("dc.dadu@sindh.demo", "admin@aab.demo"):
        assert c.put(dadu_url, headers=login(who), json=body()).status_code == 403
    assert c.get(dadu_url, headers=login("donor@fund.demo")).status_code == 403
    officer = c.get("/api/v1/fund-settings", headers=login("officer@pdma-sindh.demo")).json()
    assert officer["total"] == 13 and officer["items"][0]["thresholds"] == [40, 60, 80]
    dc = c.get("/api/v1/fund-settings", headers=login("dc.dadu@sindh.demo")).json()
    assert dc["total"] == 1 and dc["items"][0]["district_name"] == "Dadu"


def test_scenario_fund_is_isolated(ctx):
    from app.core.scenario import use_scenario
    from app.services.triggers.fund_settings import create_fund_with_tiers

    c, login, d, db = ctx
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "sindh-2022"))
    with use_scenario(sc.id):
        create_fund_with_tiers(db, d["Dadu"].id)
    db.flush()
    h = {**login("officer@pdma-sindh.demo"), "X-Scenario-Id": str(sc.id)}
    r = c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=h, json=body(allocated_pkr="1.00"))
    assert r.status_code == 200 and r.json()["scenario_id"] == str(sc.id)
    live = db.scalar(select(DisasterFund).where(DisasterFund.district_id == d["Dadu"].id,
                                                DisasterFund.scenario_id.is_(None)))
    assert str(live.allocated_pkr) == "50000000.00"


def test_tier_for_score_rule():
    cfg = TierConfig((TierRule("WATCH", 30, 10, False), TierRule("WARNING", 50, 30, False),
                      TierRule("ACTION", 70, 60, True)))
    assert [tier_for_score(s, cfg) for s in (29, 30, 55, 70)] == ["NONE", "WATCH", "WARNING", "ACTION"]
    assert cfg.cumulative_pct("WARNING") == 40 and [r.name for r in cfg.tiers_up_to("WARNING")] == ["WATCH",
                                                                                                    "WARNING"]


def test_scoring_uses_configured_thresholds(ctx):
    from app.services.forecasting.service import ForecastService
    from tests.integration.test_forecasting import RUN, put_forecast, put_obs

    c, login, d, db = ctx
    register_fund_rules()
    tiers = body()["tiers"]
    tiers = [{"name": "WATCH", "min_score": 5, "release_pct": 10}, {"name": "WARNING", "min_score": 6,
             "release_pct": 30}, {"name": "ACTION", "min_score": 7, "release_pct": 60}]
    c.put(f"/api/v1/districts/{d['Dadu'].id}/fund-settings", headers=login("officer@pdma-sindh.demo"),
          json=body(tiers=tiers))
    put_obs(db, d["Dadu"], rain=1.0, soil=0.2)
    put_forecast(db, d["Dadu"], rain=0.5)
    ForecastService().run(db, RUN, [d["Dadu"].id])
    ri = db.scalar(select(RiskIndex).where(RiskIndex.district_id == d["Dadu"].id, RiskIndex.tehsil_id.is_(None)))
    assert ri.score >= 7 and ri.tier == "ACTION"  # same low score would be NONE with 40/60/80
    assert isinstance(RUN, date)
