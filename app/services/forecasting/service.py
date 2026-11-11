"""FR3 daily scoring run (pipeline order 40). For each active district, in its FR4 mode: build features, predict
with the mode's model (or the fallback rule), weight by lead, score tehsils, aggregate to the district, map tiers,
explain, save with one RISK_SCORED audit row, commit, then hand the district RiskIndex to the trigger engine."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import settings
from app.core.errors import Conflict
from app.core.scenario import current_scenario
from app.models.forecasting import FloodForecast, RiskIndex
from app.models.geography import District, Tehsil
from app.services.explain.template_explainer import RiskExplainInputs, TemplateExplainService
from app.services.forecasting.features import PROXY_PROVENANCES, FeatureBuilder, FeatureRow, worst_provenance
from app.services.forecasting.model_registry import ModelRegistry
from app.services.forecasting.modes import ModeRegistry, ModeSpec, effective_mode
from app.services.forecasting.scoring import (
    FALLBACK_VERSION,
    aggregate_district,
    fallback_score,
    lead_weight,
    peak,
    round_half_up_score,
    tier_for,
    top_factors,
)
from app.services.ingestion.observations import active_districts
from app.services.stubs import get_audit, get_trigger_engine

log = logging.getLogger("aab.forecasting")

# FR7 registers a check here: a district whose RiskIndex for the date is referenced by a FundRelease is skipped.
RISK_LOCK_CHECKS: list[Callable[[Session, UUID, date], bool]] = []
# FR6 registers a loader returning {"WATCH": 40, "WARNING": 60, "ACTION": 80} for a district (scenario-scoped).
THRESHOLD_LOADERS: list[Callable[[Session, UUID], dict[str, int] | None]] = []


def default_thresholds() -> dict[str, int]:
    w, a, b = (int(x) for x in settings.FORECAST_DEFAULT_THRESHOLDS.split(","))
    return {"WATCH": w, "WARNING": a, "ACTION": b}


def load_thresholds(db: Session, district_id: UUID) -> dict[str, int]:
    for loader in THRESHOLD_LOADERS:
        if (t := loader(db, district_id)) is not None:
            return t
    return default_thresholds()


def _upsert(db: Session, model, values: dict[str, Any], constraint: str, keys: set[str]) -> UUID:
    stmt = insert(model).values(**values)
    stmt = stmt.on_conflict_do_update(constraint=constraint,
                                      set_={k: getattr(stmt.excluded, k) for k in values if k not in keys | {"id"}})
    return db.execute(stmt.returning(model.id)).scalar_one()


class ForecastService:
    def __init__(self, registry: ModelRegistry | None = None, builder: FeatureBuilder | None = None,
                 explainer: TemplateExplainService | None = None, clock: Clock | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.builder = builder or FeatureBuilder()
        self.explainer = explainer or TemplateExplainService()
        self._clock = clock

    def run(self, db: Session, run_date: date | None = None, district_ids: list[UUID] | None = None) -> dict[str, Any]:
        t0 = time.monotonic()
        run_date = run_date or (self._clock or get_clock()).today()
        sid = current_scenario()
        key = f"forecast:{sid or 'live'}"
        if not db.scalar(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}):
            raise Conflict("FORECAST_RUN_IN_PROGRESS", "A forecast run is already in progress.")
        res: dict[str, Any] = {"run_date": str(run_date), "scenario_id": str(sid) if sid else None,
                               "districts_scored": 0, "tehsils_scored": 0, "fallback_districts": [],
                               "stale_districts": [], "skipped_locked": [], "trigger_errors": [], "model_versions": {}}
        try:
            groups: dict[str, list[District]] = defaultdict(list)
            for d in active_districts(db, district_ids, sid):
                groups[effective_mode(db, d)].append(d)
            for mode, districts in groups.items():
                spec = ModeRegistry.get(mode)
                try:
                    model = self.registry.load(spec)
                except Exception as exc:
                    model = None
                    log.warning("model for %s unavailable, using fallback rule: %s", mode, exc)
                res["model_versions"][mode] = model.version if model else FALLBACK_VERSION
                for d in districts:
                    if any(check(db, d.id, run_date) for check in RISK_LOCK_CHECKS):
                        res["skipped_locked"].append(str(d.id))
                        get_audit().record(db, event_type="RISK_RERUN_SKIPPED", entity_type="District",
                                           entity_id=d.id, actor="system", payload={"run_date": str(run_date)})
                        db.commit()
                        continue
                    ri, n_teh = self._score_district(db, d, run_date, spec, model)
                    if ri is None:
                        continue
                    db.commit()
                    res["districts_scored"] += 1
                    res["tehsils_scored"] += n_teh
                    if ri.is_fallback:
                        res["fallback_districts"].append(d.pcode)
                    if ri.is_stale_input:
                        res["stale_districts"].append(d.pcode)
                    try:  # separate transaction: a trigger bug cannot erase scores (FR7 is idempotent)
                        get_trigger_engine().evaluate(db, d.id, ri)
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        res["trigger_errors"].append({"district_id": str(d.id), "error": str(exc)})
        finally:
            db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
        res["duration_s"] = round(time.monotonic() - t0, 2)
        return res

    # ------------------------------------------------------------------ per district
    def _predict(self, rows: list[FeatureRow], spec: ModeSpec, model) -> list[tuple[float, dict[str, float], bool,
                                                                                   str]]:
        out: list[tuple[float, dict[str, float], bool, str] | None] = [None] * len(rows)
        if model is not None:
            usable = [i for i, r in enumerate(rows) if r.has_forecast]
            feats = [rows[i].features for i in usable]
            try:
                ps, cs = model.predict_proba(feats), model.contributions(feats)
                for i, p, c in zip(usable, ps, cs, strict=True):
                    out[i] = (p, c, False, model.version)
            except Exception:
                for i in usable:
                    try:
                        out[i] = (model.predict_proba([rows[i].features])[0],
                                  model.contributions([rows[i].features])[0], False, model.version)
                    except Exception:
                        out[i] = None
        for i, r in enumerate(rows):
            if out[i] is None:  # no model, failed row, or no forecast at all (stale + fallback per clarify.md)
                p, c = fallback_score(r.features, spec)
                out[i] = (p, c, True, FALLBACK_VERSION)
        return out  # type: ignore[return-value]

    def _score_district(self, db: Session, d: District, run_date: date, spec: ModeSpec, model):
        tehsils = list(db.scalars(select(Tehsil).where(Tehsil.district_id == d.id).order_by(Tehsil.pcode)))
        if not tehsils:
            log.warning("district %s has no tehsils; skipped", d.pcode)
            return None, 0
        sid = current_scenario()
        thresholds = load_thresholds(db, d.id)
        rows = self.builder.build(db, run_date, tehsils, d, spec.features)
        preds = self._predict(rows, spec, model)
        by_teh: dict[UUID, list[tuple[FeatureRow, float, float, dict[str, float], bool, str]]] = defaultdict(list)
        for r, (p, c, fb, ver) in zip(rows, preds, strict=True):
            p_adj = p * lead_weight(spec, r.lead_day)
            by_teh[r.tehsil_id].append((r, p, p_adj, c, fb, ver))
            _upsert(db, FloodForecast, {
                "district_id": d.id, "tehsil_id": r.tehsil_id, "run_date": run_date, "lead_day": r.lead_day,
                "target_date": r.target_date, "probability": round(p_adj, 6), "mode": spec.mode.value,
                "model_version": ver, "features": {**r.as_json(), "p_raw": round(p, 6),
                                                   "lead_weight": lead_weight(spec, r.lead_day)},
                "forecast_provenance": r.provenance, "is_fallback": fb, "is_stale_input": r.is_stale,
                "scenario_id": sid}, "uq_flood_forecast", {"district_id", "tehsil_id", "run_date", "lead_day",
                                                           "scenario_id"})

        names = {t.id: t.name for t in tehsils}
        for tid, items in by_teh.items():
            self._save_index(db, d, tid, names[tid], run_date, spec, thresholds, items, None)

        agg = aggregate_district([(tid, r.lead_day, p_adj) for tid, items in by_teh.items()
                                  for r, _, p_adj, *_ in items],
                                 {t.id: t.population for t in tehsils}, settings.FORECAST_DISTRICT_MIN_POP_SHARE)
        district_items = []
        for lead in range(1, 8):
            src = agg.source_by_lead[lead]
            item = next(x for x in by_teh[src] if x[0].lead_day == lead)
            r, p, _, c, fb, ver = item
            district_items.append((r, p, agg.p_by_lead[lead], c, fb, ver))
            _upsert(db, FloodForecast, {
                "district_id": d.id, "tehsil_id": None, "run_date": run_date, "lead_day": lead,
                "target_date": r.target_date, "probability": round(agg.p_by_lead[lead], 6), "mode": spec.mode.value,
                "model_version": ver, "features": {**r.as_json(), "aggregation": {
                    "rule": agg.rule, "min_share": agg.min_share, "eligible_tehsil_ids": [str(x) for x in agg.eligible],
                    "source_tehsil_id": str(src)}},
                "forecast_provenance": r.provenance, "is_fallback": fb, "is_stale_input": r.is_stale,
                "scenario_id": sid}, "uq_flood_forecast", {"district_id", "tehsil_id", "run_date", "lead_day",
                                                           "scenario_id"})
        all_items = [x for items in by_teh.values() for x in items]
        ri = self._save_index(db, d, None, d.name, run_date, spec, thresholds, district_items, {
            "rule": agg.rule, "min_share": agg.min_share, "eligible_tehsil_ids": [str(x) for x in agg.eligible],
            "source_by_lead": {str(k): str(v) for k, v in agg.source_by_lead.items()}}, all_items)
        get_audit().record(db, event_type="RISK_SCORED", entity_type="RiskIndex", entity_id=ri.id, actor="system",
                           payload={"risk_index_id": str(ri.id), "district_id": str(d.id), "run_date": str(run_date),
                                    "score": ri.score, "tier": ri.tier, "peak_lead_day": ri.peak_lead_day,
                                    "mode": ri.mode, "model_version": ri.model_version, "is_fallback": ri.is_fallback,
                                    "is_stale_input": ri.is_stale_input,
                                    "stale_since": str(ri.stale_since) if ri.stale_since else None,
                                    "forecast_provenance": ri.forecast_provenance,
                                    "forecast_is_proxy": ri.forecast_provenance in PROXY_PROVENANCES})
        return ri, len(tehsils)

    def _save_index(self, db: Session, d: District, tehsil_id: UUID | None, name: str, run_date: date,
                    spec: ModeSpec, thresholds: dict[str, int], items, aggregation, flag_items=None) -> RiskIndex:
        flag_items = flag_items or items
        lead_p = {r.lead_day: p_adj for r, _, p_adj, *_ in items}
        peak_lead, best = peak(lead_p)
        score = round_half_up_score(best)
        tier = tier_for(score, thresholds)
        r, _, _, contrib, _, ver = next(x for x in items if x[0].lead_day == peak_lead)
        factors = top_factors(contrib, r.features)
        stale_dates = [x[0].stale_since for x in flag_items if x[0].is_stale and x[0].stale_since]
        is_stale = any(x[0].is_stale for x in flag_items)
        is_fb = any(x[4] for x in flag_items)
        prov = worst_provenance({x[0].provenance for x in flag_items if x[0].provenance})
        stale_since = min(stale_dates) if stale_dates else None
        en, ur = self.explainer.explain_risk(RiskExplainInputs(
            name, score, tier, peak_lead, r.target_date, factors, is_fb, is_stale, stale_since,
            prov in PROXY_PROVENANCES))
        sid = current_scenario()
        rid = _upsert(db, RiskIndex, {
            "district_id": d.id, "tehsil_id": tehsil_id, "run_date": run_date, "score": score,
            "peak_lead_day": peak_lead, "tier": tier, "mode": spec.mode.value,
            "model_version": FALLBACK_VERSION if is_fb and ver != FALLBACK_VERSION and tehsil_id is None else ver,
            "forecast_provenance": prov, "explanation_en": en, "explanation_ur": ur, "top_factors": factors,
            "aggregation": aggregation, "is_fallback": is_fb, "is_stale_input": is_stale, "stale_since": stale_since,
            "scenario_id": sid}, "uq_risk_index", {"district_id", "tehsil_id", "run_date", "scenario_id"})
        return db.get(RiskIndex, rid, populate_existing=True)
