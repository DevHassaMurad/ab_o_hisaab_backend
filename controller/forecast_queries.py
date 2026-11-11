"""Read side of the PDMA forecasting dashboard: summary cards, risk table, district details, map layers and
the C02 model performance panel. Everything is read from stored data; nothing is fetched per map interaction."""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select

from controller.authentication import AuthFlowError
from controller.forecasting import (
    EONET_SOURCE,
    HEAT_C,
    LABELS,
    ML,
    OM_SOURCE,
    POWER_SOURCE,
    STALE_AFTER_DAYS,
    model_version,
    today_pkt,
)
from model.forecast_model import (
    DistrictRiskAssessment,
    ForecastDistrict,
    ForecastRun,
    HazardEvent,
    HazardForecast,
    HazardObservation,
)

ACTIONS = {
    "immediate": "Urgent assessment and response required",
    "warning": "Prepare response measures",
    "watch": "Increase monitoring",
    "low": "Routine monitoring",
    "insufficient": "Check data sources",
}
PKT = timedelta(hours=5)
IMERG_SOURCE = "NASA GPM IMERG Early Run"


def iso(v):
    return v.isoformat() if v else None


def utc_iso(v):
    return (v.isoformat() + "Z") if v else None


class ForecastQueries:
    def __init__(self, db):
        self.db = db

    # Building blocks -------------------------------------------------------------------------------
    def latest(self):
        ids = select(func.max(DistrictRiskAssessment.id)).group_by(DistrictRiskAssessment.district_pcode)
        rows = self.db.scalars(select(DistrictRiskAssessment).where(DistrictRiskAssessment.id.in_(ids)))
        return {r.district_pcode: r for r in rows}

    def districts(self):
        return {d.pcode: d for d in self.db.scalars(select(ForecastDistrict))}

    def last_run(self, kind, ok_only=False):
        q = select(ForecastRun).where(ForecastRun.kind == kind)
        if ok_only:
            q = q.where(ForecastRun.status.in_(("success", "partial")))
        return self.db.scalar(q.order_by(ForecastRun.id.desc()).limit(1))

    def _forecast_map(self, variables):
        start = today_pkt()
        rows = self.db.execute(select(HazardForecast.district_pcode, HazardForecast.variable, HazardForecast.forecast_date,
                                      HazardForecast.value)
                               .where(HazardForecast.source == OM_SOURCE, HazardForecast.variable.in_(variables),
                                      HazardForecast.forecast_date >= start,
                                      HazardForecast.forecast_date < start + timedelta(days=7))).all()
        out = defaultdict(lambda: defaultdict(dict))
        for p, v, d, val in rows:
            out[p][v][d] = val
        return out

    @staticmethod
    def freshness(a):
        if a is None or a.as_of_date is None:
            return {"status": "unavailable", "age_days": None}
        age = (today_pkt() - a.as_of_date).days
        return {"status": "stale" if age > STALE_AFTER_DAYS else "current", "age_days": age}

    def _row(self, d, a, heat=None):
        cat = a.risk_category if a else "insufficient"
        return {
            "pcode": d.pcode, "district": d.name, "province": d.province, "flood_mode": d.flood_mode,
            "risk_score": a.risk_score if a else None, "risk_category": cat, "risk_label": LABELS[cat],
            "primary_hazard": "Flood", "primary_indicator": a.primary_indicator if a else None,
            "forecast_period": (f"{(a.as_of_date + timedelta(days=1)).isoformat()} to "
                                f"{(a.as_of_date + timedelta(days=7)).isoformat()}") if a and a.as_of_date else None,
            "as_of_date": iso(a.as_of_date) if a else None, "last_updated": utc_iso(a.calculated_at) if a else None,
            "freshness": self.freshness(a), "action": ACTIONS[cat], "extreme_heat": bool(heat),
        }

    # Summary cards ----------------------------------------------------------------------------------
    def summary(self, province=None):
        ds, latest = self.districts(), self.latest()
        if province:
            ds = {k: v for k, v in ds.items() if v.province == province}
        counts = {k: 0 for k in LABELS}
        valid = []
        for p in ds:
            a = latest.get(p)
            cat = a.risk_category if a else "insufficient"
            counts[cat] += 1
            if a and a.risk_score is not None:
                valid.append((a.risk_score, p))
        top = max(valid) if valid else None
        runs = {k: self.last_run(k) for k in ("nasa_power", "model", "open_meteo", "eonet", "imerg", "climatology")}
        as_of = max((a.as_of_date for a in latest.values() if a.as_of_date), default=None)
        return {
            "scope": province or "Pakistan",
            "total_districts": len(ds),
            "districts_with_valid_data": len(valid),
            "counts": counts,
            "at_risk": counts["watch"] + counts["warning"] + counts["immediate"],
            "highest_risk": {"pcode": top[1], "district": ds[top[1]].name, "province": ds[top[1]].province,
                             "risk_score": top[0], "risk_category": latest[top[1]].risk_category} if top else None,
            "overall_risk_index": round(sum(s for s, _ in valid) / len(valid), 1) if valid else None,
            "overall_method": ("Mean Model Risk Score of districts with valid data; districts with insufficient data "
                               "are excluded, never counted as zero."),
            "score_label": "Model Risk Score (%)",
            "score_note": ("Score = trained flood model probability x 100. The random-forest model is not calibrated, "
                           "so a score of 90 does not mean a 90% chance of flooding."),
            "bands": [{"key": "low", "label": LABELS["low"], "min": 0, "max": 59.9},
                      {"key": "watch", "label": LABELS["watch"], "min": 60, "max": 79.9},
                      {"key": "warning", "label": LABELS["warning"], "min": 80, "max": 89.9},
                      {"key": "immediate", "label": LABELS["immediate"], "min": 90, "max": 100}],
            "observations_as_of": iso(as_of),
            "stale": bool(as_of and (today_pkt() - as_of).days > STALE_AFTER_DAYS),
            "model_version": model_version(),
            "runs": {k: self._run(r) for k, r in runs.items()},
        }

    @staticmethod
    def _run(r):
        return None if r is None else {"status": r.status, "started_at": utc_iso(r.started_at),
                                       "finished_at": utc_iso(r.finished_at), "records": r.records, "error": r.error}

    # Monitoring table -------------------------------------------------------------------------------
    def table(self, province=None, pcode=None, category=None, hazard=None, page=1, page_size=50):
        ds, latest = self.districts(), self.latest()
        heat = self._heat_districts()
        rows = [self._row(d, latest.get(p), p in heat) for p, d in ds.items()]
        if province:
            rows = [r for r in rows if r["province"] == province]
        if pcode:
            rows = [r for r in rows if r["pcode"] == pcode]
        if category:
            rows = [r for r in rows if r["risk_category"] == category]
        if hazard == "heat":
            rows = [r for r in rows if r["extreme_heat"]]
        rows.sort(key=lambda r: (r["risk_score"] is None, -(r["risk_score"] or 0), r["district"]))
        total = len(rows)
        return {"items": rows[(page - 1) * page_size: page * page_size], "total": total, "page": page,
                "page_size": page_size, "provinces": sorted({d.province for d in ds.values()})}

    def _heat_districts(self):
        fc = self._forecast_map(["temperature_2m_max"])
        return {p for p, v in fc.items() if any((x or 0) >= HEAT_C for x in v["temperature_2m_max"].values())}

    # District details --------------------------------------------------------------------------------
    def detail(self, pcode):
        d = self.db.get(ForecastDistrict, pcode)
        if d is None:
            raise AuthFlowError("DISTRICT_NOT_FOUND", "This district does not exist.", 404)
        a = self.latest().get(pcode)
        fc = self._forecast_map(list(("precipitation_sum", "temperature_2m_max", "temperature_2m_min",
                                      "wind_speed_10m_max", "wind_gusts_10m_max", "relative_humidity_2m_mean")))[pcode]
        cur = {r.variable.replace("current_", ""): r.value for r in self.db.scalars(
            select(HazardForecast).where(HazardForecast.district_pcode == pcode, HazardForecast.source == OM_SOURCE,
                                         HazardForecast.variable.like("current_%"),
                                         HazardForecast.forecast_date == today_pkt()))}
        days = sorted(fc["precipitation_sum"].keys())
        rain = [fc["precipitation_sum"].get(x) for x in days]
        timeline = [{
            "date": x.isoformat(), "precipitation_mm": fc["precipitation_sum"].get(x),
            "temp_max_c": fc["temperature_2m_max"].get(x), "temp_min_c": fc["temperature_2m_min"].get(x),
            "wind_max_kmh": fc["wind_speed_10m_max"].get(x), "gust_max_kmh": fc["wind_gusts_10m_max"].get(x),
            "humidity_pct": fc["relative_humidity_2m_mean"].get(x),
            "extreme_heat": (fc["temperature_2m_max"].get(x) or 0) >= HEAT_C,
        } for x in days]

        def total(n):
            vals = [v for v in rain[:n] if v is not None]
            return round(sum(vals), 1) if len(vals) == min(n, len(rain)) and vals else None

        since = datetime.utcnow() - timedelta(days=30)
        events = [{"category": e.category, "title": e.title, "event_date": utc_iso(e.event_date), "link": e.link,
                   "source": e.source} for e in self.db.scalars(
            select(HazardEvent).where(HazardEvent.district_pcode == pcode, HazardEvent.event_date >= since)
            .order_by(HazardEvent.event_date.desc()))]
        imerg = self.db.execute(select(HazardObservation.observation_date, HazardObservation.value)
                                .where(HazardObservation.district_pcode == pcode, HazardObservation.source == IMERG_SOURCE,
                                       HazardObservation.variable == "precipitation")
                                .order_by(HazardObservation.observation_date.desc()).limit(7)).all()
        history = self.db.execute(select(DistrictRiskAssessment.as_of_date, func.max(DistrictRiskAssessment.risk_score))
                                  .where(DistrictRiskAssessment.district_pcode == pcode,
                                         DistrictRiskAssessment.as_of_date.is_not(None))
                                  .group_by(DistrictRiskAssessment.as_of_date)
                                  .order_by(DistrictRiskAssessment.as_of_date.desc()).limit(14)).all()
        inputs = (a.calculation_details or {}).get("inputs", {}) if a else {}
        fc_run, power_run = self.last_run("open_meteo", True), self.last_run("nasa_power", True)
        return {
            **self._row(d, a, any(t["extreme_heat"] for t in timeline)),
            "probability": a.probability if a else None,
            "model_version": a.model_version if a else model_version(),
            "data_completeness": a.data_completeness if a else 0.0,
            "calculation": a.calculation_details if a else None,
            "weather": {
                "source": OM_SOURCE, "retrieved_at": utc_iso(fc_run.finished_at) if fc_run else None, "kind": "forecast",
                "current_temp_c": cur.get("temperature_2m"), "current_humidity_pct": cur.get("relative_humidity_2m"),
                "current_wind_kmh": cur.get("wind_speed_10m"),
                "today_max_c": timeline[0]["temp_max_c"] if timeline else None,
                "today_min_c": timeline[0]["temp_min_c"] if timeline else None,
                "forecast_rain_24h_mm": total(1), "forecast_rain_3d_mm": total(3), "forecast_rain_7d_mm": total(7),
            },
            "observed": {
                "source": POWER_SOURCE, "kind": "observation", "as_of_date": iso(a.as_of_date) if a else None,
                "retrieved_at": utc_iso(power_run.finished_at) if power_run else None,
                "rain_1d_mm": inputs.get("rain_1d"), "rain_3d_mm": inputs.get("rain_3d"),
                "rain_7d_mm": inputs.get("rain_7d"), "rain_30d_mm": inputs.get("rain_30d"),
                "rain_7d_vs_normal": inputs.get("rain_7d_anom"),
                "soil_wetness": {"surface": inputs.get("gwettop"), "root_zone": inputs.get("gwetroot"),
                                 "profile": inputs.get("gwetprof")},
                "saturation_vs_q99": {"surface": inputs.get("gwettop_rel_q99"), "root_zone": inputs.get("gwetroot_rel_q99"),
                                      "profile": inputs.get("gwetprof_rel_q99")},
                "imerg_daily_mm": [{"date": iso(x), "precipitation_mm": round(v, 2)} for x, v in imerg],
            },
            "flood": {
                "observed_status": "Flood event reported" if any(e["category"] == "flood" for e in events)
                else "No satellite-reported flood event in the last 30 days",
                "observed_source": EONET_SOURCE,
                "observed_note": ("Observed events come from NASA EONET satellite/agency reports. They are observations, "
                                  "not forecasts, and are not confirmed ground reports."),
                "nowcast": None, "nowcast_note": "NASA flood nowcast is not integrated: no accessible data endpoint.",
                "model_note": ("Rainfall-related flood risk comes from the trained model (next 7 days), shown as the "
                               "Model Risk Score."),
            },
            "events": events,
            "other_hazards": {"extreme_heat_days": [t["date"] for t in timeline if t["extreme_heat"]],
                              "heat_threshold_c": HEAT_C, "source": OM_SOURCE},
            "timeline": timeline,
            "timeline_note": ("Daily weather is the Open-Meteo forecast. The model produces one risk score for the "
                              "whole next-7-day window, so no per-day model score is shown."),
            "score_history": [{"as_of_date": iso(x), "risk_score": s} for x, s in reversed(history)],
        }

    # Layers --------------------------------------------------------------------------------------
    def layers(self):
        power, model, om, eo, imerg = (self.last_run(k, True) for k in ("nasa_power", "model", "open_meteo", "eonet",
                                                                         "imerg"))
        ev = dict(self.db.execute(select(HazardEvent.category, func.count(HazardEvent.id))
                                  .where(HazardEvent.event_date >= datetime.utcnow() - timedelta(days=60))
                                  .group_by(HazardEvent.category)).all())

        def L(key, name, source, kind, run, available=True, note=None, legend=None):
            return {"key": key, "name": name, "source": source, "kind": kind, "available": bool(run) and available,
                    "updated_at": utc_iso(run.finished_at) if run else None, "note": note, "legend": legend}

        return [
            L("risk", "Model Risk Score", f"Trained C02 flood model on {POWER_SOURCE}", "forecast", model,
              legend="bands", note="Next 7 days. Uncalibrated model score, not a probability."),
            L("rain_observed", "Rainfall monitoring (last 7 days)",
              IMERG_SOURCE if imerg else POWER_SOURCE, "observation", imerg or power, legend="rain_7d"),
            L("rain_forecast", "7-day rainfall forecast", OM_SOURCE, "forecast", om, legend="rain_fc"),
            L("flood_observed", "Observed flood events", EONET_SOURCE, "observation", eo, available=True,
              note=f"{ev.get('flood', 0)} reported in the last 60 days."),
            L("flood_nowcast", "Flood nowcast", "NASA Floods Nowcast", "nowcast", None, available=False,
              note="Not available: no accessible data endpoint for automated retrieval."),
            L("landslide", "Landslide events", EONET_SOURCE, "observation", eo,
              note=f"{ev.get('landslide', 0)} reported in the last 60 days. Nowcast not available."),
            L("heat", "Extreme heat (forecast max ≥ 45 °C)", OM_SOURCE, "forecast", om, legend="heat"),
        ]

    def layer_data(self, key):
        if key == "risk":
            latest = self.latest()
            return {"districts": {p: {"value": a.risk_score, "category": a.risk_category} for p, a in latest.items()}}
        if key == "rain_observed":
            src = IMERG_SOURCE if self.last_run("imerg", True) else POWER_SOURCE
            if src == IMERG_SOURCE:
                since = today_pkt() - timedelta(days=10)
                rows = self.db.execute(select(HazardObservation.district_pcode, HazardObservation.observation_date,
                                              HazardObservation.value)
                                       .where(HazardObservation.source == IMERG_SOURCE,
                                              HazardObservation.observation_date >= since)).all()
                by = defaultdict(dict)
                for p, x, v in rows:
                    by[p][x] = v
                out = {p: {"value": round(sum(sorted(v.items())[-7:][i][1] for i in range(min(7, len(v)))), 1),
                           "days": min(7, len(v))} for p, v in by.items()}
            else:
                out = {p: {"value": (a.calculation_details or {}).get("inputs", {}).get("rain_7d")}
                       for p, a in self.latest().items()}
            return {"unit": "mm", "source": src, "districts": out}
        if key == "rain_forecast":
            fc = self._forecast_map(["precipitation_sum"])
            return {"unit": "mm", "districts": {p: {"value": round(sum(x for x in v["precipitation_sum"].values()
                                                                        if x is not None), 1)} for p, v in fc.items()}}
        if key == "heat":
            fc = self._forecast_map(["temperature_2m_max"])
            return {"unit": "°C", "threshold": HEAT_C, "districts": {
                p: {"value": max((x for x in v["temperature_2m_max"].values() if x is not None), default=None)}
                for p, v in fc.items()}}
        cats = {"flood_observed": "flood", "landslide": "landslide"}
        if key in cats:
            since = datetime.utcnow() - timedelta(days=60)
            return {"points": [{"lat": e.lat, "lon": e.lon, "title": e.title, "event_date": utc_iso(e.event_date),
                                "district_pcode": e.district_pcode, "link": e.link, "source": e.source}
                               for e in self.db.scalars(select(HazardEvent).where(HazardEvent.category == cats[key],
                                                                                  HazardEvent.event_date >= since))]}
        raise AuthFlowError("LAYER_NOT_AVAILABLE", "This layer is not available.", 404)

    # Model performance (historical C02 evaluation, read from the ML project's reports) --------------
    @staticmethod
    def model_performance():
        rep = ML / "reports"

        def read(name):
            p = rep / name
            return json.loads(p.read_text()) if p.exists() else None

        t, splits, sel = read("test_results.json"), read("splits_summary.json"), read("selected_model.json")
        if not t:
            return {"available": False}
        cm = t["confusion_matrix"]
        pos, neg = cm["TP"] + cm["FN"], cm["TN"] + cm["FP"]
        evaluated = datetime.utcfromtimestamp((rep / "test_results.json").stat().st_mtime)
        return {
            "available": True,
            "challenge": "C02 - Severe Class Imbalance / Rare-Event Stress",
            "status": "PASS" if t["pr_auc_average_precision"] >= 0.70 else "FAIL",
            "requirement": "PR-AUC >= 0.70 on a 99:1 negative-to-positive evaluation split",
            "model": t["model"], "model_version": model_version(),
            "evaluation_split": {"years": (splits or {}).get("test", {}).get("years"), "rows": t["test_rows"],
                                 "positives": t["test_positives"], "negatives": t["test_negatives"],
                                 "ratio": t["test_ratio"],
                                 "method": (splits or {}).get("test", {}).get("set_99to1", {}).get("method")},
            "pr_auc": t["pr_auc_average_precision"], "no_skill_pr_auc": t["no_skill_pr_auc_baseline"],
            "precision": t["precision"], "recall": t["recall"], "f1": t["f1"],
            "threshold": t["threshold_from_validation"],
            "confusion_matrix": cm,
            "positive_detection_rate": cm["TP"] / pos if pos else None,
            "false_negative_rate": cm["FN"] / pos if pos else None,
            "false_positive_rate": cm["FP"] / neg if neg else None,
            "majority_class_baseline": t.get("all_negative_predictor"),
            "predicted_positive_rate": t.get("predicted_positive_rate"),
            "pr_auc_by_flood_mode": t.get("pr_auc_by_flood_mode"),
            "events": {"in_test": t.get("events_in_test"), "detected": t.get("events_detected_at_least_once"),
                       "district_events": t.get("district_events_detected")},
            "train_years": (splits or {}).get("train", {}).get("years"),
            "validation_pr_auc_99": (sel or {}).get("val99_pr_auc"),
            "last_evaluated_at": utc_iso(evaluated),
            "calibration": ("Uncalibrated random-forest probabilities: live values are shown as Model Risk Scores, "
                            "not calibrated flood probabilities."),
            "note": ("Historical evaluation of the trained model. It is separate from the live district forecasts "
                     "and does not mean 99% accuracy."),
        }

    def runs(self, limit=20):
        return [{"id": r.id, "kind": r.kind, "trigger": r.trigger, **self._run(r)} for r in
                self.db.scalars(select(ForecastRun).order_by(ForecastRun.id.desc()).limit(limit))]
