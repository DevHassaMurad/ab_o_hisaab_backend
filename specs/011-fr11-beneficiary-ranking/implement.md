# Implementation Guide: FR11 Beneficiary Ranking

## Execution order

| Step | Tasks | Notes |
|---|---|---|
| 1. Setup | T001–T003 | Fixtures include FR17 reserve spy and AlertService spy |
| 2. Schema and types | T004–T009 | Migration 0011; rules models; dataclasses; schemas; router |
| 3. Tests for ranking | T010–T014 | Commit failing |
| 4. Ranking + reasons | T015–T018 | Pure functions first |
| 5. `allocate` | T019–T021 | Needs FR12 `compute_targets`, `fill_release` |
| 6. Fairness + finalize | T022–T023 (tests) → T024–T027 | |
| 7. Action gate | T028 (tests) → T029 | Principle II |
| 8. Overrides | T030–T031 (tests) → T032–T035 | Needs FR12 `override_limit`, `refill_release` |
| 9. Rules | T036–T037 (tests) → T038–T039 | |
| 10. Polish | T040–T043 | |

## Key algorithms

### Normalisation and score (`ranking.py`)

```python
from dataclasses import dataclass
from decimal import Decimal

def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x

@dataclass(frozen=True)
class Factors:
    values: dict[str, float]          # e.g. {"poverty": .9, "family_size": .636364, ...}
    raw: dict[str, object]
    imputed: tuple[str, ...]

def urgency(score: int, peak_lead_day: int, decay: float) -> float:
    return _clamp(score / 100 * (1 - decay * (peak_lead_day - 1)))

def normalise_household(b: Beneficiary, ctx: AllocationContext) -> Factors:
    n = ctx.rules.norms
    imputed: list[str] = []
    p = b.poverty_score
    if p is None:
        p = ctx.poverty_median_by_tehsil.get(b.tehsil_id) or ctx.poverty_median_district or 0.5
        imputed.append("poverty_score")
    risk = ctx.risk_by_tehsil.get(b.tehsil_id) or ctx.district_risk        # TehsilRisk(score, lead, source, id)
    s = min(float(b.existing_support_pkr) / float(n.support_norm_pkr), 1.0)
    return Factors(
        values={
            "poverty": round(_clamp(p), 6),
            "family_size": round((min(b.family_size, n.family_size_cap) - 1) / (n.family_size_cap - 1), 6),
            "exposure": round(_clamp(b.flood_exposure), 6),
            "urgency": round(urgency(risk.score, risk.peak_lead_day, n.urgency_lead_decay), 6),
            "support": round(s, 6),
        },
        raw={"poverty_score": b.poverty_score, "family_size": b.family_size, "flood_exposure": b.flood_exposure,
             "existing_support_pkr": str(b.existing_support_pkr), "tehsil_risk_score": risk.score,
             "peak_lead_day": risk.peak_lead_day, "urgency_source": risk.source, "risk_index_id": str(risk.id)},
        imputed=tuple(imputed + (["urgency"] if risk.source == "DISTRICT" else [])),
    )

def normalise_shopkeeper(b: Beneficiary, ctx: AllocationContext) -> Factors:
    n = ctx.rules.norms
    risk = ctx.risk_by_tehsil.get(b.tehsil_id) or ctx.district_risk
    return Factors(
        values={
            "stock_at_risk": round(min(float(b.stock_value_pkr) / float(n.stock_norm_pkr), 1.0), 6),
            "exposure": round(_clamp(b.flood_exposure), 6),
            "urgency": round(urgency(risk.score, risk.peak_lead_day, n.urgency_lead_decay), 6),
            "support": round(min(float(b.existing_support_pkr) / float(n.support_norm_pkr), 1.0), 6),
        },
        raw={...},
        imputed=("urgency",) if risk.source == "DISTRICT" else (),
    )

def priority_score(f: Factors, w: dict[str, float]) -> tuple[float, dict[str, float]]:
    contrib = {k: w[k] * v for k, v in f.values.items() if k != "support"}
    contrib["support"] = -w["support_penalty"] * f.values["support"]
    score = round(100 * _clamp(sum(contrib.values())), 2)
    return score, {k: round(100 * v, 2) for k, v in contrib.items()}
```

Worked examples (unit test T011), tehsil risk 85, peak lead day 1 → urgency 0.85:

| Who | Factors | Sum | Score |
|---|---|---|---|
| HH A | p .90, f 7/11, x .80, u .85, s 0 | .36 + .127273 + .24 + .085 = .812273 | **81.23** |
| HH B | p .60, f 4/11, x .50, u .85, s 1.0 | .24 + .072727 + .15 + .085 − .25 = .297727 | **29.77** |
| SK S1 | k .80, x .70, u .85, s 0 | .32 + .21 + .255 = .785 | **78.50** |
| SK S2 | k .24, x .90, u .85, s .20 | .096 + .27 + .255 − .05 = .571 | **57.10** |

### Sort and rank (`ranking.py`)

```python
def sort_key(c: RankedCandidate) -> tuple:
    v, b = c.factors.values, c.beneficiary
    if b.type == "HOUSEHOLD":
        return (-c.priority_score, -v["poverty"], -v["exposure"], -b.family_size,
                b.existing_support_pkr, b.register_ref)
    return (-c.priority_score, -v["exposure"], -v["urgency"], b.existing_support_pkr,
            -b.stock_value_pkr, b.register_ref)

def rank(candidates: list[Candidate], ctx: AllocationContext) -> dict[str, list[RankedCandidate]]:
    out: dict[str, list[RankedCandidate]] = {"HOUSEHOLD": [], "SHOPKEEPER": []}
    for c in candidates:
        t = c.beneficiary.type
        f = normalise_household(c.beneficiary, ctx) if t == "HOUSEHOLD" else normalise_shopkeeper(c.beneficiary, ctx)
        score, contrib = priority_score(f, ctx.rules.weights[t])
        out[t].append(RankedCandidate(c, f, score, contrib))
    for t, rows in out.items():
        rows.sort(key=sort_key)
        for i, r in enumerate(rows, start=1):
            r.rank = i
    return out
```

Determinism: input order is irrelevant because the key is total (`register_ref` unique). `Decimal` and `int` compare exactly; scores are pre-rounded to 2 dp.

### `allocate` (`service.py`)

```python
class AllocationServiceImpl:                       # implements AllocationService Protocol
    def allocate(self, db, fund_release: FundRelease) -> list[Allocation]:
        rel = db.get(FundRelease, fund_release.id, with_for_update=True)
        existing = db.scalars(select(Allocation).where(Allocation.fund_release_id == rel.id)
                              .order_by(Allocation.rank)).all()
        if existing:
            return list(existing)                                  # idempotent, no audit
        if rel.status == "REJECTED":
            raise Conflict(code="RELEASE_REJECTED")
        if rel.status not in ("PENDING_APPROVAL",):                # Action keeps PENDING_APPROVAL / APPROVED
            rel.status = "ALLOCATING" if rel.tier != "ACTION" else rel.status
        ctx = build_context(db, rel)
        candidates = load_eligible(db, ctx)                         # FR10 eligible_query on ctx.tehsil_ids
        targets = amounts.compute_targets(db, ctx, candidates)      # FR12 -> {beneficiary_id: Target}
        live = [c.with_target(targets[c.beneficiary.id]) for c in candidates
                if targets[c.beneficiary.id].topup_pkr > 0]
        ranked = rank(live, ctx)
        filled = fill.fill_release(ranked["HOUSEHOLD"], ranked["SHOPKEEPER"],
                                   household_pool=rel.household_pool_pkr, shopkeeper_pool=rel.shopkeeper_pool_pkr,
                                   spillover=ctx.spillover_enabled, mode=ctx.fill_mode)   # FR12
        rows = self._persist(db, rel, filled, ctx)                  # Allocation rows incl. score_inputs
        build_reasons(rows, ctx, filled)                            # sets reason_en / reason_ur
        fc = run_fairness_check(db, rel, rows, ctx)
        AuditService.record(db, event_type="ALLOCATION_COMPUTED", entity_type="FundRelease", entity_id=rel.id,
                            actor="system", payload=computed_payload(rel, rows, ctx, fc,
                                                                     already_covered=count_by_type(candidates, live)))
        # already_covered = {"HOUSEHOLD": n, "SHOPKEEPER": m}; FR12's summary reads it from this payload
        if not ctx.tier_requires_approval:
            self.finalize(db, rel, actor="system")                  # may return HELD
        return rows
```

`_persist` uses `db.add_all` (or `insert().values([...])` for > 2 000 rows) and sets `scenario_id = rel.scenario_id`, `computed_amount_pkr = target.topup_pkr`, `final_amount_pkr` and `status` from the fill result.

### `finalize` (`service.py`)

```python
    def finalize(self, db, fund_release, *, actor: str, acknowledge_fairness: bool = False,
                 note: str | None = None) -> FinalizeResult:
        rel = db.get(FundRelease, fund_release.id, with_for_update=True)
        if rel.allocation_saved_at is not None:
            return FinalizeResult("ALREADY_SAVED")
        if rel.status == "REJECTED":
            raise Conflict(code="RELEASE_REJECTED")
        tier = get_tier(db, rel)
        if tier.requires_approval:                                   # Principle II, never bypassed
            appr = db.scalar(select(Approval).where(Approval.fund_release_id == rel.id).with_for_update())
            if appr is None or appr.decision != "APPROVED":
                raise Conflict(code="APPROVAL_REQUIRED")
        fc = db.scalar(select(FairnessCheck).where(FairnessCheck.fund_release_id == rel.id).with_for_update())
        if not fc.passed and fc.acknowledged_at is None:
            if acknowledge_fairness:
                validate_ack_note(note)
                fc.acknowledged_by, fc.acknowledged_at, fc.acknowledgement_note = actor, clock.now(), note
                AuditService.record(db, event_type="FAIRNESS_ACKNOWLEDGED", entity_type="FairnessCheck",
                                    entity_id=fc.id, actor=actor,
                                    payload={"fund_release_id": str(rel.id), "flags": fc.metrics["flags"],
                                             "note": note, "simulated": False})
            elif rel.scenario_id is not None and settings.REPLAY_AUTO_ACK_FAIRNESS:
                fc.acknowledged_at = clock.now(); fc.acknowledgement_note = "Simulated acknowledgement (replay)"
                fc.metrics = {**fc.metrics, "acknowledged_simulated": True}
                AuditService.record(db, event_type="FAIRNESS_ACKNOWLEDGED", entity_type="FairnessCheck",
                                    entity_id=fc.id, actor="replay-sim",
                                    payload={"fund_release_id": str(rel.id), "flags": fc.metrics["flags"],
                                             "simulated": True})
            else:
                self._notify_hold_once(db, rel, fc)                  # AlertService.enqueue(ESCALATION, "fairness_hold")
                return FinalizeResult("HELD", reason="FAIRNESS_ACK_REQUIRED")
        rel.allocation_saved_at, rel.allocation_saved_by = clock.now(), actor
        AuditService.record(db, event_type="ALLOCATION_SAVED", entity_type="FundRelease", entity_id=rel.id,
                            actor=actor, payload=saved_payload(db, rel, fc))
        n = payment_reserver.reserve_for_release(db, rel)            # FR17, same transaction
        return FinalizeResult("SAVED", payments_reserved=n)
```

`acknowledged_by` is a User FK; the router passes the officer's user id as `actor` and sets `acknowledged_by` from it. `actor="system"` never acknowledges.

### Fairness check (`fairness.py`)

```python
def run_fairness_check(db, rel: FundRelease, rows: list[Allocation], ctx: AllocationContext) -> FairnessCheck:
    th = ctx.rules.fairness
    funded = defaultdict(Decimal)
    for a in rows:
        if a.status == "FUNDED":
            funded[ctx.tehsil_of[a.beneficiary_id]] += a.final_amount_pkr
    total_funded = sum(funded.values(), Decimal(0))
    need = {t: ctx.tehsils[t].population * ctx.risk_for(t) * ctx.district.vulnerability_weight for t in ctx.tehsil_ids}
    total_need = sum(need.values())
    tehsils, flags, notes = [], 0, []
    for t in sorted(ctx.tehsil_ids, key=lambda t: ctx.tehsils[t].pcode):
        st = ctx.register_stats.get(t)                               # FR10 TehsilRegisterStats
        expected = need[t] / total_need if total_need else 0.0
        actual = float(funded[t] / total_funded) if total_funded else 0.0
        ratio = actual / expected if expected else None
        sparse = (st is None or st.households == 0 or st.relative_coverage < th.sparse_relative_coverage
                  or st.completeness < th.min_completeness)
        flag = None
        if total_funded and ratio is not None and ratio < th.min_ratio and expected >= th.min_expected_share:
            flag = "SPARSE_UNDERFUNDED" if sparse else "UNDERFUNDED_INFO"
            flags += flag == "SPARSE_UNDERFUNDED"
        tehsils.append({...})                                        # shape in plan.md
    if not total_funded:
        notes.append("NO_FUNDED_ROWS")
    metrics = {"method": "fair-v1", "thresholds": th.model_dump(), "total_funded_pkr": str(total_funded),
               "tehsils": tehsils, "notes": notes, "flags": flags,
               "min_ratio_observed": min((x["ratio"] for x in tehsils if x["ratio"] is not None), default=None),
               "acknowledged_simulated": False}
    fc = upsert_fairness_check(db, rel, passed=(flags == 0), metrics=metrics)  # clears acknowledgement on change
    return fc
```

### Override (`service.py` + `overrides.py`)

```python
_PHONE = re.compile(r"(\+92|0)?3\d{2}[\s-]?\d{7}")
_CNIC = re.compile(r"\d{5}-?\d{7}-?\d")

def validate_override_reason(reason: str | None) -> str:
    r = (reason or "").strip()
    if len(r) < settings.OVERRIDE_REASON_MIN_CHARS or len(r) > settings.OVERRIDE_REASON_MAX_CHARS:
        raise Unprocessable(code="OVERRIDE_REASON_REQUIRED")
    if _PHONE.search(r) or _CNIC.search(r):
        raise Unprocessable(code="REASON_CONTAINS_PII")
    return r

    def override(self, db, allocation_id, *, final_amount_pkr: Decimal, reason: str, actor: User) -> OverrideResult:
        a = db.get(Allocation, allocation_id)
        rel = db.get(FundRelease, a.fund_release_id, with_for_update=True)
        if rel.allocation_saved_at:  raise Conflict(code="ALLOCATION_LOCKED")
        if rel.status == "REJECTED": raise Conflict(code="RELEASE_REJECTED")
        reason = validate_override_reason(reason)
        if final_amount_pkr % 100 != 0 or final_amount_pkr < 0:
            raise Unprocessable(code="AMOUNT_NOT_ROUNDED")
        limit = amounts.override_limit(db, rel, a)                     # FR12
        if final_amount_pkr > limit:
            raise Unprocessable(code="AMOUNT_ABOVE_LIMIT", extra={"limit_pkr": str(limit)})
        before = snapshot(db, rel)                                     # {allocation_id: (status, final)}
        prev = a.final_amount_pkr
        a.final_amount_pkr, a.override_reason = final_amount_pkr, reason
        a.overridden_by, a.overridden_at = actor.id, clock.now()
        a.status = "FUNDED" if final_amount_pkr > 0 else "EXCLUDED"
        fill.refill_release(db, rel)                                   # FR12; raises OVERRIDE_EXCEEDS_POOL -> rollback
        changed = diff(before, snapshot(db, rel))
        build_reasons(changed_rows(db, changed | {a.id}), build_context(db, rel), None)
        fc = run_fairness_check(db, rel, load_rows(db, rel), build_context(db, rel))
        AuditService.record(db, event_type="ALLOCATION_OVERRIDDEN", entity_type="Allocation", entity_id=a.id,
                            actor=str(actor.id), payload={"fund_release_id": str(rel.id),
                            "beneficiary_id": str(a.beneficiary_id), "computed_pkr": str(a.computed_amount_pkr),
                            "previous_final_pkr": str(prev), "final_pkr": str(final_amount_pkr), "reason": reason,
                            "cascade": cascade_counts(changed), "list_sha256": list_sha256(db, rel)})
        return OverrideResult(a, changed, fc)
```

The router wraps the call in one transaction; `OVERRIDE_EXCEEDS_POOL` from FR12 aborts it, so nothing changes.

### Reason templates (`app/services/explain/allocation_templates.py`)

```python
TIER_UR = {"WATCH": "نگرانی", "WARNING": "انتباہ", "ACTION": "کارروائی"}
TIER_EN = {"WATCH": "Watch", "WARNING": "Warning", "ACTION": "Action"}
LEVEL_UR = {"high": "زیادہ", "medium": "درمیانہ", "low": "کم"}

def level(x: float) -> str:
    return "high" if x >= 0.67 else "medium" if x >= 0.34 else "low"

def pkr(d: Decimal) -> str:
    return f"{int(d):,}"

HH_FUNDED_EN = ("Rank {rank} of {n} households in {tehsil}. Priority {score}/100: poverty {pov} ({pov_lvl}), "
                "{family} family members, flood exposure {exp} ({exp_lvl}), flood risk {risk}/100 in {lead} days, "
                "earlier support PKR {support}. Amount PKR {amount}: {tier} tier household amount PKR {target} "
                "minus PKR {prior} already paid in this flood event.")
HH_FUNDED_UR = ("{tehsil} کے {n} گھرانوں میں آپ کا نمبر {rank} ہے۔ ترجیحی اسکور {score}/100۔ وجوہات: غربت {pov} ({pov_lvl_ur})، "
                "گھر کے افراد {family}، سیلاب کا خطرہ {exp} ({exp_lvl_ur})، {lead} دن میں سیلاب کا امکان {risk}/100، "
                "پہلے ملی امداد {support} روپے۔ رقم {amount} روپے: {tier_ur} درجے کی گھریلو رقم {target} روپے، "
                "اس سیلاب میں پہلے ادا شدہ {prior} روپے منہا کر کے۔")
SK_FUNDED_EN = ("Rank {rank} of {n} shopkeepers in {tehsil}. Priority {score}/100: stock at risk PKR {stock}, "
                "flood exposure {exp} ({exp_lvl}), flood risk {risk}/100 in {lead} days, earlier support PKR {support}. "
                "Amount PKR {amount}: {pct}% of stock, capped at PKR {cap}{prior_en}.")
SK_FUNDED_UR = ("{tehsil} کے {n} دکانداروں میں آپ کا نمبر {rank} ہے۔ ترجیحی اسکور {score}/100۔ وجوہات: خطرے میں مال {stock} روپے، "
                "سیلاب کا خطرہ {exp} ({exp_lvl_ur})، {lead} دن میں سیلاب کا امکان {risk}/100، پہلے ملی امداد {support} روپے۔ "
                "رقم {amount} روپے: مال کا {pct} فیصد، زیادہ سے زیادہ حد {cap} روپے{prior_ur}۔")
UNMET_EN = ("Rank {rank} of {n} {type_en}. Not funded this time: the {type_en} budget for this {tier} release ran out "
            "after rank {last_funded}. Recorded as unmet need of PKR {amount}.")
UNMET_UR = ("{n} میں سے درجہ {rank}۔ اس بار رقم نہیں مل سکی کیونکہ اس {tier_ur} قسط کا بجٹ درجہ {last_funded} کے بعد ختم ہو گیا۔ "
            "{amount} روپے کی ضرورت درج کر لی گئی ہے۔")
EXCLUDED_EN = "Excluded from this release by the PDMA officer. Reason: {reason}."
EXCLUDED_UR = "پی ڈی ایم اے افسر نے اس قسط سے خارج کیا۔ وجہ: {reason}۔"
OVERRIDE_EN = " Amount changed by the PDMA officer from PKR {computed} to PKR {final}. Reason: {reason}."
OVERRIDE_UR = " پی ڈی ایم اے افسر نے رقم {computed} روپے سے بدل کر {final} روپے کی۔ وجہ: {reason}۔"

def render_allocation_reason(i: AllocationReasonInputs) -> tuple[str, str]:
    """Pure function: same inputs -> same text. Returns (en, ur)."""
```

`prior_en`/`prior_ur` are empty unless the shopkeeper got an earlier partial payment in the event (`", minus PKR {prior} already paid"` / `"، پہلے ادا شدہ {prior} روپے منہا کر کے"`). Numbers use Western digits with thousands separators; the Urdu reviewer may switch to Urdu digits later (single helper).

## Configuration

| Key | Default |
|---|---|
| `RANKING_DEFAULT_WEIGHTS` | HH 0.40/0.20/0.30/0.10, penalty 0.25; SK 0.40/0.30/0.30, penalty 0.25 |
| `RANKING_NORMS` | family cap 12, stock norm 250 000, support norm 50 000, lead decay 0.05 |
| `FAIRNESS_THRESHOLDS` | min ratio 0.5, min expected share 0.05, sparse relative coverage 0.5, min completeness 0.7 |
| `ALLOCATION_TEHSIL_SCOPE` | `AFFECTED` |
| `OVERRIDE_REASON_MIN_CHARS` / `_MAX_CHARS` | 10 / 500 |
| `REPLAY_AUTO_ACK_FAIRNESS` | true |

## Commands

```bash
alembic upgrade head
pytest tests/unit/allocation -q                       # ranking, fairness, reasons (+ FR12 amounts/fill)
pytest tests/integration/allocation -q
pytest tests/contract/test_allocations_api.py tests/contract/test_allocation_rules_api.py -q
pytest -k "finalize_gate" -q                          # Principle II gate only
pytest -m slow tests/integration/allocation/test_allocate_flow.py
```

## Verification checklist

| Acceptance scenario | Verified by |
|---|---|
| US1-1, US1-2 worked scores | `test_ranking.py::test_worked_examples` |
| US1-3 determinism | `test_ranking.py::test_shuffle_invariance` (200 seeds) |
| US1-4 tie-break chain | `test_ranking.py::test_tie_break_household`, `::test_tie_break_shopkeeper` |
| US1-5 reasons EN/UR | `test_reasons.py`, `test_allocate_flow.py::test_reasons_present` |
| US2-1 auto-save | `test_finalize_gate.py::test_watch_balanced_autosaves` |
| US2-2 hold + alert | `test_finalize_gate.py::test_sparse_holds_and_alerts` |
| US2-3 ack and save | `test_finalize_gate.py::test_ack_then_save` |
| US2-4 save without ack | `test_finalize_gate.py::test_save_without_ack_409` |
| US2-5 info-only | `test_fairness.py::test_underfunded_not_sparse_passes` |
| US3-1..5 | `test_override.py` |
| US4-1..4 | `test_finalize_gate.py::test_action_*` |
| US5-1..3 | `test_allocation_rules_api.py` |
| SC-004 across replays | Manual: after `sindh-2022` and `buner-2025` replays, `SELECT` Action releases with `allocation_saved_at` and no APPROVED approval → 0 rows |

## Demo notes

- In the `sindh-2022` replay, when Dadu hits Warning, open the ranked list: top household and the hero shopkeeper persona with Urdu reasons. Point at "Rank 1 of 1,240 … Priority 81.23/100 because …".
- Show the fairness panel (green). Optional: switch to a prepared fixture district with a sparse tehsil to show the amber warning and the acknowledgement (only if time allows).
- At Action, while waiting for the DC's WhatsApp approval, override one row with a reason to show the cascade, then let the DC approve; payments start.

## Pitfalls

- Do not sort on unrounded floats; the display would disagree with the order.
- `Decimal` vs `float`: factors are floats; money stays `Decimal`. Never feed money through float into amounts.
- Do not let `allocate` write a second `ALLOCATION_COMPUTED` on the idempotent path.
- `finalize` must take row locks on `FundRelease`, `Approval` and `FairnessCheck` in that order everywhere (deadlock avoidance with FR8).
- Re-running fairness after an override must clear a previous acknowledgement, or an officer could acknowledge a mild skew and then override into a severe one.
- The fairness-hold alert must be sent once per release (check for an existing `Alert` with `template_key="fairness_hold"` and the release id).
- Replay rows must carry `scenario_id`; FR11 queries for `RiskIndex` must filter by the same `scenario_id`.
