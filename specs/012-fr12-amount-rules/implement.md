# Implementation Guide: FR12 Amount Rules

## Execution order

| Step | Tasks | Notes |
|---|---|---|
| 1. Setup | T001–T002 | |
| 2. Foundations | T003–T005 | Enum migration; dataclasses; `round_pkr` |
| 3. Tests (pure) | T006, T007, T011, T013, T014 | All commit failing |
| 4. Targets | T012 → T009 → T010 | Shopkeeper first (no DB), then household, then prior + `compute_targets` |
| 5. Fill | T016 | Pure function |
| 6. Integration tests | T008, T015 | Need FR11 `allocate` (or the thin harness) |
| 7. Overrides | T017 → T018, T019 | |
| 8. Summary | T020 → T021–T023 | |
| 9. Polish | T024–T026 | |

## Key algorithms

### Rounding (`amounts.py`)

```python
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

STEP = Decimal(settings.AMOUNT_ROUNDING_STEP_PKR)        # 100

def round_pkr(amount: Decimal, step: Decimal = STEP, mode: str = ROUND_HALF_UP) -> Decimal:
    """>>> round_pkr(Decimal("24493.6"))
    Decimal('24500')
    >>> round_pkr(Decimal("24449.99"))
    Decimal('24400')
    """
    return (amount / step).quantize(Decimal(1), rounding=mode) * step

def floor_pkr(amount: Decimal, step: Decimal = STEP) -> Decimal:
    return round_pkr(amount, step, ROUND_FLOOR)
```

### Targets (`amounts.py`)

```python
@dataclass(frozen=True)
class Target:
    rule: str                      # HOUSEHOLD_FLAT_TOPUP | SHOPKEEPER_CAPPED_EVENT
    target_pkr: Decimal
    prior_event_pkr: Decimal
    topup_pkr: Decimal
    detail: dict                   # stock, pct, raw, cap, capped, warnings

def household_target(fund: DisasterFund, tier: str) -> tuple[Decimal, list[str]]:
    warnings: list[str] = []
    raw = fund.household_amount_by_tier.get(tier)
    if raw is None:
        return Decimal(0), ["AMOUNT_TIER_MISSING"]
    value = Decimal(str(raw))
    rounded = round_pkr(value)
    if rounded != value:
        warnings.append("AMOUNT_ROUNDED")
    return max(rounded, Decimal(0)), warnings

def shopkeeper_target(fund: DisasterFund, stock_value_pkr: Decimal) -> tuple[Decimal, dict]:
    pct = Decimal(fund.shopkeeper_stock_pct)
    raw = stock_value_pkr * pct / Decimal(100)
    cap = floor_pkr(Decimal(fund.shopkeeper_cap_pkr))
    rounded = round_pkr(raw)
    target = min(rounded, cap)
    return target, {"stock_value_pkr": str(stock_value_pkr), "stock_pct": int(pct), "raw_pkr": str(raw),
                    "cap_pkr": str(cap), "capped": rounded > cap}

def prior_event_amounts(db, rel: FundRelease, beneficiary_ids: list[UUID]) -> dict[UUID, Decimal]:
    other = aliased(FundRelease)
    q = (select(Allocation.beneficiary_id, func.coalesce(func.sum(Allocation.final_amount_pkr), 0))
         .join(other, other.id == Allocation.fund_release_id)
         .outerjoin(Payment, Payment.allocation_id == Allocation.id)
         .where(other.event_id == rel.event_id,
                other.id != rel.id,
                other.status != "REJECTED",
                Allocation.status == "FUNDED",
                or_(Payment.id.is_(None), Payment.status != "CANCELLED"),
                Allocation.beneficiary_id.in_(beneficiary_ids))
         .group_by(Allocation.beneficiary_id))
    return {bid: Decimal(total) for bid, total in db.execute(q)}

def compute_targets(db, ctx: AllocationContext, candidates: list[Candidate]) -> dict[UUID, Target]:
    prior = prior_event_amounts(db, ctx.release, [c.beneficiary.id for c in candidates])
    hh_target, hh_warn = household_target(ctx.fund, ctx.release.tier)
    out: dict[UUID, Target] = {}
    for c in candidates:
        b = c.beneficiary
        p = prior.get(b.id, Decimal(0))
        if b.type == "HOUSEHOLD":
            t, detail, rule = hh_target, {"warnings": hh_warn}, "HOUSEHOLD_FLAT_TOPUP"
        else:
            t, detail = shopkeeper_target(ctx.fund, b.stock_value_pkr)
            rule = "SHOPKEEPER_CAPPED_EVENT"
        out[b.id] = Target(rule, t, p, max(Decimal(0), t - p), detail)
    return out

def override_limit(db, rel: FundRelease, a: Allocation) -> Decimal:
    fund = load_fund(db, rel)
    b = db.get(Beneficiary, a.beneficiary_id)
    prior = prior_event_amounts(db, rel, [b.id]).get(b.id, Decimal(0))
    if b.type == "HOUSEHOLD":
        top = max((household_target(fund, t)[0] for t in ("WATCH", "WARNING", "ACTION")), default=Decimal(0))
    else:
        top = floor_pkr(Decimal(fund.shopkeeper_cap_pkr))
    return max(Decimal(0), top - prior)
```

### Fill (`fill.py`)

```python
@dataclass
class FillRow:
    key: UUID                      # beneficiary id
    rank: int
    amount: Decimal                # top-up (computed_amount_pkr)
    pinned: Decimal | None = None  # override amount, or None
    status: str = "UNMET_NEED"
    remaining_before: Decimal = Decimal(0)
    pass_no: int = 1

@dataclass
class PoolFill:
    rows: list[FillRow]
    pool: Decimal
    remaining: Decimal
    spill_in: Decimal = Decimal(0)
    spill_out: Decimal = Decimal(0)

class OverrideExceedsPool(Conflict):
    code = "OVERRIDE_EXCEEDS_POOL"

def _start(rows: list[FillRow], pool: Decimal) -> PoolFill:
    pinned_total = sum((r.pinned for r in rows if r.pinned is not None), Decimal(0))
    if pinned_total > pool:
        raise OverrideExceedsPool()
    for r in rows:
        if r.pinned is not None:
            r.status = "FUNDED" if r.pinned > 0 else "EXCLUDED"
    return PoolFill(rows=sorted(rows, key=lambda r: r.rank), pool=pool, remaining=pool - pinned_total)

def _strict_continue(pf: PoolFill, pass_no: int) -> None:
    """Fund unpinned UNMET rows from the first one, in rank order, until one does not fit."""
    for r in pf.rows:
        if r.pinned is not None or r.status == "FUNDED":
            continue
        if r.amount <= pf.remaining:
            r.remaining_before, r.pass_no = pf.remaining, pass_no
            r.status = "FUNDED"
            pf.remaining -= r.amount
        else:
            r.remaining_before = pf.remaining
            break                                       # STRICT: everyone after stays UNMET_NEED

def fill_release(households: list[FillRow], shopkeepers: list[FillRow], *, household_pool: Decimal,
                 shopkeeper_pool: Decimal, spillover: bool, mode: str = "STRICT") -> ReleaseFill:
    if mode != "STRICT":
        raise NotImplementedError("SKIP_AHEAD is roadmap (clarify open item 1)")
    hh, sk = _start(households, household_pool), _start(shopkeepers, shopkeeper_pool)
    _strict_continue(hh, 1)
    _strict_continue(sk, 1)
    if spillover:
        move = hh.remaining                              # household -> shopkeeper
        hh.remaining -= move; hh.spill_out += move
        sk.remaining += move; sk.spill_in += move
        _strict_continue(sk, 2)
        move = sk.remaining                              # shopkeeper -> household
        sk.remaining -= move; sk.spill_out += move
        hh.remaining += move; hh.spill_in += move
        _strict_continue(hh, 2)
    return ReleaseFill(household=hh, shopkeeper=sk, unused=hh.remaining + sk.remaining)
```

Note on the second move: in the spec example the shopkeeper pool's remaining after pass 2 (2 000) goes to households, which cannot fund the next 7 000 household, so `hh.remaining` = 2 000 = `unused`. Summary shows `spillover_out` for shopkeepers = 2 000 and `spillover_in` for households = 2 000.

Worked example (unit test T013, spec US3-2):

| Step | Households (pool 20 000; 7 000 each) | Shopkeepers (pool 100 000; 50 000, 24 000, 30 000, 20 000) |
|---|---|---|
| Pass 1 | r1 F (13 000 left), r2 F (6 000), r3 U | r1 F (50 000), r2 F (26 000), r3 U (30 000 > 26 000), r4 U |
| HH → SK | out 6 000, remaining 0 | in 6 000 → 32 000; r3 F (2 000 left); r4 U (20 000 > 2 000) |
| SK → HH | in 2 000 → 2 000; r3 U (7 000 > 2 000) | out 2 000, remaining 0 |
| Result | funded 14 000 (2), unmet 7 000 (1) | funded 104 000 (3), unmet 20 000 (1) |
| Unused | 2 000 stays in the fund | |

### Re-fill after override (`fill.py`)

```python
def refill_release(db, rel: FundRelease) -> ReleaseFill:
    rows = db.scalars(select(Allocation).where(Allocation.fund_release_id == rel.id)
                      .order_by(Allocation.rank).with_for_update()).all()
    by_type = {"HOUSEHOLD": [], "SHOPKEEPER": []}
    for a in rows:
        by_type[a.beneficiary_type].append(FillRow(key=a.id, rank=a.rank, amount=a.computed_amount_pkr,
                                                   pinned=a.final_amount_pkr if a.overridden_by else None))
    res = fill_release(by_type["HOUSEHOLD"], by_type["SHOPKEEPER"],
                       household_pool=rel.household_pool_pkr, shopkeeper_pool=rel.shopkeeper_pool_pkr,
                       spillover=spillover_enabled(rel), mode=settings.ALLOCATION_FILL_MODE)
    write_back(db, rows, res)          # unpinned: final = computed, status from fill; score_inputs.fill updated
    return res
```

`a.beneficiary_type` comes from a join on `Beneficiary.type` (not a stored column). `spillover_enabled(rel)` reads FR9's per-fund setting, falling back to `ALLOCATION_POOL_SPILLOVER`.

### Summary (`summary.py`)

```python
def release_summary(db, rel: FundRelease) -> AllocationSummaryOut:
    agg = db.execute(select(Beneficiary.type, Allocation.status, func.count(), func.sum(Allocation.final_amount_pkr),
                            func.max(case((Allocation.status == "FUNDED", Allocation.rank))))
                     .join(Beneficiary).where(Allocation.fund_release_id == rel.id)
                     .group_by(Beneficiary.type, Allocation.status)).all()
    computed = db.scalar(select(AuditLog.payload).where(AuditLog.entity_id == rel.id,
                                                        AuditLog.event_type == "ALLOCATION_COMPUTED")
                         .order_by(AuditLog.seq.desc()).limit(1))
    already = (computed or {}).get("already_covered", {"HOUSEHOLD": 0, "SHOPKEEPER": 0})
    spill = spill_from_rows(db, rel)   # max(score_inputs.fill.spillover_in_pkr) per pool, out derived
    ...
```

FR11 must write `already_covered` per type in the `ALLOCATION_COMPUTED` payload (agreed in FR11 plan).

## Configuration

| Key | Default |
|---|---|
| `AMOUNT_ROUNDING_STEP_PKR` | 100 |
| `ALLOCATION_FILL_MODE` | `STRICT` |
| `HOUSEHOLD_TIER_MODE` | `TOPUP_TO_TARGET` |
| `SHOPKEEPER_TIER_MODE` | `EVENT_ONCE` |
| `ALLOCATION_POOL_SPILLOVER` | `true` (fallback if FR9 has no per-fund policy) |
| Fund fields (FR6) | `household_amount_by_tier` 3000/7000/15000, `shopkeeper_cap_pkr` 50 000, `shopkeeper_stock_pct` 40 |

## Commands

```bash
alembic upgrade head                                   # 0012 adds EXCLUDED
pytest tests/unit/allocation/test_amounts.py tests/unit/allocation/test_topup.py tests/unit/allocation/test_fill.py -q
pytest tests/integration/allocation/test_budget_short.py tests/integration/allocation/test_repeated_tiers.py -q
pytest tests/contract/test_allocation_summary_api.py -q
pytest -k invariants -q                                # property test, 500 seeds
python -m doctest app/services/allocation/amounts.py
```

## Verification checklist

| Acceptance scenario | Verified by |
|---|---|
| US1-1 household 7 000 | `test_amounts.py::test_household_target_warning` |
| US1-2 Watch amount 0 | `test_repeated_tiers.py::test_zero_watch_amount_no_household_rows` |
| US1-3 rounding 3 050 | `test_amounts.py::test_household_target_rounds` |
| US2-1..3 | `test_amounts.py::test_shopkeeper_*` |
| US3-1 strict, no spillover | `test_fill.py::test_strict_no_spillover` |
| US3-2 spillover example | `test_fill.py::test_spec_example_spillover` |
| US3-3 invariants | `test_fill.py::test_invariants` |
| US4-1..5 | `test_repeated_tiers.py` |
| US5-1..3 | `test_fill.py::test_refill_*`, `test_budget_short.py::test_override_*` |
| US6-1..2 | `test_allocation_summary_api.py` |
| SC-002 totals per event | `test_repeated_tiers.py::test_event_totals_never_exceed_targets` |

## Demo notes

- On the Action approval screen (FR8) the DC sees the summary total (e.g. "PKR 18.6 M to 1 240 households and 52 shopkeepers; 310 households unmet need").
- In the `sindh-2022` replay, point at a household's three rows: 3 000 at Watch, 4 000 at Warning, 8 000 at Action, total 15 000 ("we top up, we never pay twice").
- Unmet need appears as a number, not a hidden gap: "PKR 2.2 M unmet need recorded".

## Pitfalls

- `Decimal(0.4)` from a float is `0.4000000000000000222…`. Build Decimals from `int` or `str` only (`Decimal(str(x))`).
- JSONB returns numbers as `int`/`float`; convert `household_amount_by_tier` values with `Decimal(str(v))`.
- `ALTER TYPE … ADD VALUE` cannot run inside a transaction block in older Postgres versions; use `op.execute` with `autocommit_block()` in Alembic.
- Do not include the current release in `prior_event_amounts` (`other.id != rel.id`), or a re-fill would see its own rows as prior.
- Releases of the same day must be allocated in tier order; FR7 owns the call order, but add an assertion in `compute_targets` that no higher-tier release of the event already has rows when allocating a lower tier.
- Keep `fill_release` pure (no DB); it is the function the property test hammers.
