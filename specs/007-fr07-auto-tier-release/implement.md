# Implement Guide: FR7 Automatic Tier Release

## Execution order

| Step | Tasks | Output |
|---|---|---|
| 1 | T001–T003 | Migration, test doubles, config |
| 2 | T004–T006 (red) → T007–T008 (green) | `amounts.py`, transitions |
| 3 | T009–T012 (red) → T013–T016 (green) | Watch/Warning end to end with stubs |
| 4 | T017–T019 → T020–T021 | Skipped tiers + Action gate |
| 5 | T022–T023 → T024–T025 | DC and beneficiary alerts |
| 6 | T026–T030 → T031–T032 | Lifecycle, idempotency, recovery |
| 7 | T033–T034 → T035–T036 | API |
| 8 | T037–T040 | Registry, backtests, perf, lint |

## Key algorithms

### Amounts (`app/services/triggers/amounts.py`)

```python
WHOLE = Decimal("1")
EARMARKED = ("PENDING_APPROVAL", "APPROVED", "ALLOCATING")

def round_pkr(x: Decimal) -> Decimal:
    return x.quantize(WHOLE, rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))

def cumulative_target(allocated_pkr: Decimal, cum_pct: int) -> Decimal:
    if cum_pct >= 100:
        return allocated_pkr                                  # exact, keeps paisa
    return round_pkr(allocated_pkr * cum_pct / Decimal(100))

@dataclass(frozen=True)
class AmountCalc:
    cumulative_pct: int
    allocated_pkr_at_fire: Decimal
    target_pkr: Decimal
    prior_released_pkr: Decimal
    uncapped_pkr: Decimal
    available_pkr: Decimal
    capped: bool
    amount_pkr: Decimal
    def as_json(self) -> dict[str, object]: ...              # Decimals → str

def compute_tier_amount(fund: DisasterFund, config: TierConfig, tier: TierName,
                        prior_released: Decimal, available: Decimal) -> AmountCalc:
    cum = config.cumulative_pct(tier)
    target = cumulative_target(fund.allocated_pkr, cum)
    uncapped = max(Decimal("0.00"), target - prior_released)
    avail = max(Decimal("0.00"), available)
    amount = min(uncapped, avail)
    return AmountCalc(cum, fund.allocated_pkr, target, prior_released, uncapped, avail, amount < uncapped, amount)

def available_to_release(db: Session, fund: DisasterFund) -> Decimal:
    earmarked = db.scalar(select(func.coalesce(func.sum(FundRelease.amount_pkr), 0))
                          .where(FundRelease.fund_id == fund.id, FundRelease.status.in_(EARMARKED)))
    return fund.remaining_pkr - Decimal(earmarked)

def planned_tier_amounts(allocated_pkr: Decimal, config: TierConfig) -> dict[TierName, Decimal]:
    out, prior = {}, Decimal("0.00")
    for name in TIER_ORDER:
        target = cumulative_target(allocated_pkr, config.cumulative_pct(name))
        out[name], prior = max(Decimal("0.00"), target - prior), target
    return out
```

### Episode bookkeeping (`app/services/triggers/events.py`)

```python
def get_open_event(db, district_id, scenario_id, *, for_update=True) -> TriggerEvent | None:
    q = select(TriggerEvent).where(TriggerEvent.district_id == district_id,
                                   TriggerEvent.status == "OPEN",
                                   scenario_filter(TriggerEvent, scenario_id))
    return db.scalar(q.with_for_update() if for_update else q)

def open_event(db, district_id, scenario_id, risk_index, tier, clock) -> TriggerEvent:
    ev = TriggerEvent(district_id=district_id, opened_at=clock.now(), status="OPEN",
                      highest_tier=tier, current_tier="NONE", below_watch_streak=0,
                      confirmation_status="PENDING", scenario_id=scenario_id)
    db.add(ev); db.flush()
    audit.record(db, event_type="TRIGGER_EVENT_OPENED", entity_type="TriggerEvent", entity_id=ev.id,
                 actor="system", payload={"district_id": str(district_id), "run_date": risk_index.run_date.isoformat(),
                                          "score": risk_index.score, "tier": tier})
    return ev

def record_day(ev: TriggerEvent, run_date: date, tier: TierLevel) -> None:
    new_day = ev.last_evaluated_date != run_date
    if tier != "NONE":
        ev.below_watch_streak = 0
    elif new_day:
        ev.below_watch_streak += 1
    ev.last_evaluated_date = run_date

def close_event_if_due(db, ev: TriggerEvent, fund: DisasterFund, clock) -> bool:
    if ev.below_watch_streak < fund.event_close_days:
        return False
    if any(r.status == "PENDING_APPROVAL" for r in ev.releases):
        return False                                     # never strand an approval
    ev.status, ev.closed_at = "CLOSED", clock.now()
    audit.record(db, event_type="TRIGGER_EVENT_CLOSED", entity_type="TriggerEvent", entity_id=ev.id,
                 actor="system", payload={"closed_on": clock.today().isoformat(),
                                          "below_watch_streak": ev.below_watch_streak,
                                          "highest_tier": ev.highest_tier,
                                          "released_total_pkr": str(released_total(ev))})
    return True
```

### Engine (`app/services/triggers/engine.py`)

```python
class DefaultTriggerEngine:                                   # implements interfaces.TriggerEngine
    def __init__(self, audit, alerts, allocation, approvals, payments, clock, settings): ...

    def evaluate(self, db: Session, district_id: UUID, risk_index: RiskIndex) -> list[FundRelease]:
        scenario_id = current_scenario()
        fund = lock_fund(db, district_id, scenario_id)          # SELECT … FOR UPDATE: serialises per district
        config = load_tier_config(db, fund)
        tier = tier_for_score(risk_index.score, config)
        if risk_index.tier != tier:
            log.warning("tier_mismatch", stored=risk_index.tier, computed=tier)

        ev = get_open_event(db, district_id, scenario_id)
        if ev is None:
            if tier == "NONE":
                return []                                       # quiet day, nothing to track
            ev = open_event(db, district_id, scenario_id, risk_index, tier, self.clock)
        elif ev.last_evaluated_date and risk_index.run_date < ev.last_evaluated_date:
            log.warning("out_of_order_run_date", event=ev.id, run_date=risk_index.run_date)
            return []

        fired: list[FundRelease] = []
        with db.begin_nested():                                 # firing savepoint
            fired_tiers = {r.tier for r in ev.releases}
            for rule in config.tiers_up_to(tier):               # WATCH → … → today's tier
                if rule.name not in fired_tiers:
                    fired.append(self._fire_tier(db, ev, fund, config, rule, risk_index))
            if tier_rank(tier) > tier_rank(ev.highest_tier):
                ev.highest_tier = tier
            prev = ev.current_tier
            record_day(ev, risk_index.run_date, tier)
            if tier != prev:
                ev.current_tier = tier
                self._notify_dc_tier_change(db, ev, prev, tier, risk_index, fired)
            close_event_if_due(db, ev, fund, self.clock)

        for release in fired + self._unfinished(ev, exclude=fired):   # resume heals earlier failures
            if release.status in ("ALLOCATING", "APPROVED"):
                run_release_pipeline(db, release, actor="system")
        return fired

    def _fire_tier(self, db, ev, fund, config, rule, risk_index) -> FundRelease:
        prior = sum((r.amount_pkr for r in ev.releases if r.status != "REJECTED"), Decimal("0.00"))
        calc = compute_tier_amount(fund, config, rule.name, prior, available_to_release(db, fund))
        pools = split_pools(calc.amount_pkr, fund.household_share_pct)          # FR9
        release = FundRelease(
            event_id=ev.id, fund_id=fund.id, tier=rule.name,
            amount_pkr=calc.amount_pkr,
            household_pool_pkr=pools.household_pkr, shopkeeper_pool_pkr=pools.shopkeeper_pkr,
            split_details=pools.as_json(),                                       # FR9 proposed field
            purpose=self.settings.fund_defaults.purposes[rule.name],
            risk_index_id=risk_index.id, amount_calc=calc.as_json(),
            status="PENDING_APPROVAL" if rule.requires_approval else "ALLOCATING",
            simulated=current_scenario() is not None or self.settings.PAYMENT_RAIL_SIMULATED,
            scenario_id=current_scenario())
        db.add(release); db.flush()
        ev.releases.append(release)
        self.audit.record(db, event_type="TIER_FIRED", entity_type="FundRelease", entity_id=release.id,
                          actor="system", payload={
                              "event_id": str(ev.id), "district_id": str(ev.district_id), "tier": rule.name,
                              "score": risk_index.score, "risk_index_id": str(risk_index.id),
                              "run_date": risk_index.run_date.isoformat(), "amount_calc": calc.as_json(),
                              "amount_pkr": str(calc.amount_pkr), "pools": pools.as_json(),
                              "status": release.status, "requires_approval": rule.requires_approval,
                              "is_fallback": risk_index.is_fallback, "is_stale_input": risk_index.is_stale_input,
                              "simulated": release.simulated})
        if rule.requires_approval:
            self.approvals.request_approval(db, release)                         # FR8; never allocates
        return release

    def _notify_dc_tier_change(self, db, ev, prev, tier, risk_index, fired) -> None:
        self.audit.record(db, event_type="TIER_CHANGED", entity_type="TriggerEvent", entity_id=ev.id,
                          actor="system", payload={"from_tier": prev, "to_tier": tier,
                                                   "score": risk_index.score,
                                                   "run_date": risk_index.run_date.isoformat()})
        dc = find_district_dc(db, ev.district_id)                               # User role=DC
        if dc is None:
            log.warning("no_dc_for_district", district=ev.district_id); return
        top = fired[-1] if fired else None
        self.alerts.enqueue(db, recipient=dc, purpose="TIER_ALERT", template_key="tier_change_dc",
                            context={"from_tier": prev, "to_tier": tier, "score": risk_index.score,
                                     "peak_lead_day": risk_index.peak_lead_day,
                                     "amount_pkr": str(top.amount_pkr) if top else None,
                                     "purpose": top.purpose if top else None,
                                     "explanation_ur": risk_index.explanation_ur,
                                     "explanation_en": risk_index.explanation_en,
                                     "is_fallback": risk_index.is_fallback,
                                     "is_stale": risk_index.is_stale_input},
                            related={"fund_release_id": top.id if top else None})
```

### Release pipeline (`app/services/triggers/release_pipeline.py`)

```python
RELEASE_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_APPROVAL": {"APPROVED", "REJECTED"},
    "APPROVED": {"ALLOCATING"},
    "ALLOCATING": {"RELEASED", "PARTIALLY_RELEASED"},
}

class ApprovalRequired(Conflict):
    code = "APPROVAL_REQUIRED"

def transition_release(db, release, to_status, actor, note="", extra=None) -> None:
    if to_status not in RELEASE_TRANSITIONS.get(release.status, set()):
        raise Conflict("INVALID_TRANSITION", f"{release.status} → {to_status}")
    frm, release.status = release.status, to_status
    if to_status in ("RELEASED", "PARTIALLY_RELEASED"):
        release.released_at = clock.now()
    audit.record(db, event_type="RELEASE_STATUS_CHANGED", entity_type="FundRelease", entity_id=release.id,
                 actor=actor, payload={"from_status": frm, "to_status": to_status, "note": note, **(extra or {})})

def run_release_pipeline(db, release, actor="system") -> FundRelease:
    if release.status == "APPROVED":
        transition_release(db, release, "ALLOCATING", actor, "approved; allocating")
    if release.status != "ALLOCATING":
        raise ApprovalRequired(f"Release {release.id} is {release.status}")    # Principle II gate
    try:
        with db.begin_nested():
            allocations = existing_allocations(db, release) or allocation_service.allocate(db, release)
            if fairness_pending(db, release):           # FairnessCheck.passed=False and not acknowledged
                release.pipeline_error = None
                return release                          # FR11 calls complete_release after ack
            complete_release(db, release, allocations, actor)
            release.pipeline_error = None
    except Exception as exc:                            # savepoint rolled back; firing kept
        release.pipeline_error = f"{type(exc).__name__}: {exc}"[:500]
        log.exception("release_pipeline_failed", release=release.id)
    return release

def complete_release(db, release, allocations, actor) -> FundRelease:
    dispatch_release_payments(db, release, allocations)        # FR17: idempotent per allocation
    _alert_beneficiaries(db, release, allocations)
    unmet = any(a.status == "UNMET_NEED" for a in allocations)
    capped = bool(release.amount_calc.get("capped"))
    counts = {"FUNDED": sum(a.status == "FUNDED" for a in allocations),
              "UNMET_NEED": sum(a.status == "UNMET_NEED" for a in allocations)}
    transition_release(db, release, "PARTIALLY_RELEASED" if (unmet or capped) else "RELEASED",
                       actor, "allocations saved", {"allocation_counts": counts})
    return release

def _alert_beneficiaries(db, release, allocations) -> None:
    for a in allocations:
        if alert_exists(db, a.beneficiary_id, release.id, purpose="TIER_ALERT"):
            continue                                            # resume-safe
        alerts.enqueue(db, recipient=a.beneficiary, purpose="TIER_ALERT",
                       template_key=f"tier_alert_{release.tier.lower()}",
                       context={"tier": release.tier,
                                "amount_pkr": str(a.final_amount_pkr) if a.status == "FUNDED" else None,
                                "purpose": release.purpose},
                       related={"fund_release_id": release.id})
```

## Configuration

| Key | Where | Default |
|---|---|---|
| `fund_defaults.purposes` | `data/seeds/funds.yaml` | see plan.md |
| `fund_defaults.event_close_days` | seed → `DisasterFund.event_close_days` | 14 |
| `PAYMENT_RAIL_SIMULATED` | env / `app/core/config.py` | `true` |
| `RELEASE_STALLED_AFTER_MIN` | env / `app/core/config.py` | `10` |

## Commands

```bash
alembic upgrade head
pytest tests/unit/triggers/test_amounts.py tests/unit/triggers/test_release_transitions.py -q
pytest tests/integration/triggers -k "engine or lifecycle or pipeline or releases_api" -q
pytest tests/contract/test_releases_contract.py -q
pytest tests/backtest -k trigger -q          # needs FR5 cached data
pytest -m perf tests/integration/triggers/test_engine.py
```

## Verification checklist

| Scenario | Test / step |
|---|---|
| US1-1 Watch 5 M, pools 3.5 M/1.5 M | `test_engine.py::test_first_watch_fires` |
| US1-2 Warning only 15 M | `test_engine.py::test_warning_increment_only` |
| US1-3 no refire at same tier | `test_engine.py::test_no_refire_same_tier` |
| US1-4 RELEASED vs PARTIALLY_RELEASED | `test_release_pipeline.py::test_final_status_*` |
| US2-1 jump to 85 → 3 releases, Action pending | `test_engine.py::test_jump_fires_all_lower_tiers` |
| US2-2 Action gate | `test_release_pipeline.py::test_action_never_allocated_without_approval` |
| US2-3 rejected Action not re-requested | `test_engine.py::test_rejected_action_not_refired` |
| US3-1/2/3 DC alerts on change only | `test_engine.py::test_dc_alert_on_tier_change` |
| US4-1 idempotent day | `test_engine_idempotency.py` |
| US4-2/3/4 close rules, new episode | `test_event_lifecycle.py` |
| US4-5 replay isolation | `test_event_lifecycle.py::test_scenario_isolation` |
| US5-1/2 list, resume | `test_releases_api.py` |
| SC-001 backtests | `tests/backtest/test_trigger_backtests.py` |
| SC-004 exact sums | `test_amounts.py::test_property_sum_equals_allocated` |

## Demo notes

In the Sindh 2022 replay the presenter steps day by day. When Dadu's score crosses 40, the map turns yellow, the Watch release (PKR 5 M, "Preposition boats and food") appears on the Releases panel and the DC phone gets the tier alert. A few days later the jump past 60 adds the PKR 15 M Warning release. When it crosses 80, the Action release appears as **Pending approval** with PKR 30 M, and FR8 takes over (live WhatsApp approval or simulated). The control-2021 replay shows the map staying green and an empty Releases panel.

## Pitfalls

- Never compute tiers from `release_pct` alone; always use `cumulative_pct` and subtract prior releases.
- `ev.releases` must include releases created earlier in the same call (append after flush), or Warning's amount will double-count Watch.
- Do not alert inside the pipeline savepoint for the DC tier change; the DC alert belongs to the firing savepoint so it survives pipeline failures.
- The fund `FOR UPDATE` lock must be taken before reading the open event, or concurrent evaluate calls can each open an event (the partial unique index would then raise).
- `run_release_pipeline` must be idempotent: reuse existing allocations; FR17 dispatch must ignore allocations that already have a Payment.
- Use `Clock`, never `datetime.utcnow()`; replay timestamps come from `ReplayClock`.
- The Action release amount is computed at firing time; approval later does not recompute it.
