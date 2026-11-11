"""FR1 US4 with a test indicator file (the committed CSV is empty until real PSLM/MICS data is curated)."""
import httpx
from sqlalchemy import func, select

from app.models.core import AuditLog
from app.models.geography import District
from app.models.ingestion import ContextIndicator, DataSourceStatus
from app.services.ingestion.context_indicators import ContextIndicatorService

CSV = """district_pcode,indicator,value,period,source
PK-SD-DAD,poverty_headcount,0.61,2019-20,test
PK-SD-DAD,housing_kutcha_pct,0.70,2017,test
PK-SD-DAD,literacy_rate,0.40,2019-20,test
PK-SD-LRK,poverty_headcount,0.30,2019-20,test
PK-SD-LRK,housing_kutcha_pct,0.40,2017,test
PK-SD-LRK,literacy_rate,0.60,2019-20,test
"""


def wb(ok=True):
    def handler(_):
        if not ok:
            return httpx.Response(503)
        return httpx.Response(200, json=[{}, [{"date": "2018", "value": 21.9}]])
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_upserts_and_audits_weights(seeded, tmp_path):
    db = seeded
    f = tmp_path / "ci.csv"
    f.write_text(CSV)
    res = ContextIndicatorService(http=wb()).refresh(db, f)
    assert res.indicators_upserted == 6 and res.world_bank_healthy
    dadu = db.scalar(select(District).where(District.pcode == "PK-SD-DAD"))
    larkana = db.scalar(select(District).where(District.pcode == "PK-SD-LRK"))
    assert dadu.vulnerability_weight > larkana.vulnerability_weight
    assert dadu.vulnerability_inputs["indicators"]["poverty_headcount"]["raw"] == 0.61
    assert db.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.event_type == "VULNERABILITY_WEIGHT_UPDATED")) >= 2
    ContextIndicatorService(http=wb()).refresh(db, f)  # idempotent
    assert db.scalar(select(func.count()).select_from(ContextIndicator)) == 6


def test_world_bank_down_keeps_indicators(seeded, tmp_path):
    db = seeded
    f = tmp_path / "ci.csv"
    f.write_text(CSV)
    res = ContextIndicatorService(http=wb(ok=False)).refresh(db, f)
    assert res.indicators_upserted == 6 and not res.world_bank_healthy
    assert db.scalar(select(DataSourceStatus.is_healthy).where(DataSourceStatus.source == "WORLD_BANK")) is False
