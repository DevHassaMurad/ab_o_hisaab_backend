# Implement Guide: FR9 Household and Shopkeeper Split

## Execution order

| Step | Tasks | Output |
|---|---|---|
| 1 | T001–T003 | Migration, defaults, types |
| 2 | T010–T011 (red) → T013 (green) | `split_pools` (unblocks FR7) |
| 3 | T004–T006 → T007–T009 | Validation hook, preview |
| 4 | T014–T016 → T017–T018 | Spillover + outcome record (unblocks FR12) |
| 5 | T019–T020 → T021–T022 | Breakdown endpoint |
| 6 | T023–T025 | Registry, cross-FR test, lint |

## Key algorithms (`app/services/triggers/split.py`)

### Validation

```python
def validate_split(household_share_pct: object) -> list[FieldError]:
    ok = (isinstance(household_share_pct, int) and not isinstance(household_share_pct, bool)
          and 0 <= household_share_pct <= 100)
    return [] if ok else [FieldError("household_share_pct", "SPLIT_PCT_INVALID",
                                     "Household share must be a whole number from 0 to 100.")]
```

### Pools and rounding

```python
ZERO = Decimal("0.00")

def _whole(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))

def split_pools(amount_pkr: Decimal, household_share_pct: int) -> PoolSplit:
    if amount_pkr < 0:
        raise ValueError("amount must be >= 0")
    exact = amount_pkr * household_share_pct / Decimal(100)
    if household_share_pct == 0:
        household = ZERO
    elif household_share_pct == 100:
        household = amount_pkr
    else:
        household = min(max(_whole(exact), ZERO), amount_pkr)
    return PoolSplit(amount_pkr=amount_pkr, household_share_pct=household_share_pct,
                     household_pkr=household, shopkeeper_pkr=amount_pkr - household,
                     household_exact_pkr=exact.quantize(Decimal("0.01")))
```

### Spillover (pure, single step, simultaneous)

```python
def plan_spillover(pools: PoolSplit, usage: PoolUsage, *, enabled: bool) -> SpilloverPlan:
    left_h = pools.household_pkr - usage.household_used_pkr
    left_s = pools.shopkeeper_pkr - usage.shopkeeper_used_pkr
    if left_h < 0 or left_s < 0:
        raise SplitInvariantError("usage exceeds pool")
    moves: list[Move] = []
    if enabled:
        if left_h > 0 and usage.shopkeeper_unmet_pkr > 0 and pools.household_share_pct < 100:
            moves.append(Move("HOUSEHOLD", "SHOPKEEPER", min(left_h, usage.shopkeeper_unmet_pkr)))
        if left_s > 0 and usage.household_unmet_pkr > 0 and pools.household_share_pct > 0:
            moves.append(Move("SHOPKEEPER", "HOUSEHOLD", min(left_s, usage.household_unmet_pkr)))
    def net(t: BeneficiaryType) -> Decimal:
        return (sum((m.amount_pkr for m in moves if m.to_type == t), ZERO)
                - sum((m.amount_pkr for m in moves if m.from_type == t), ZERO))
    return SpilloverPlan(enabled=enabled, moves=tuple(moves),
                         effective_household_pkr=pools.household_pkr + net("HOUSEHOLD"),
                         effective_shopkeeper_pkr=pools.shopkeeper_pkr + net("SHOPKEEPER"))

def usage_from_allocations(allocations: Iterable[Allocation]) -> PoolUsage:
    acc = {("HOUSEHOLD", "FUNDED"): ZERO, ("SHOPKEEPER", "FUNDED"): ZERO,
           ("HOUSEHOLD", "UNMET_NEED"): ZERO, ("SHOPKEEPER", "UNMET_NEED"): ZERO}
    for a in allocations:
        amt = a.final_amount_pkr if a.status == "FUNDED" else a.computed_amount_pkr
        acc[(a.beneficiary.type, a.status)] += amt
    return PoolUsage(acc[("HOUSEHOLD", "FUNDED")], acc[("SHOPKEEPER", "FUNDED")],
                     acc[("HOUSEHOLD", "UNMET_NEED")], acc[("SHOPKEEPER", "UNMET_NEED")])
```

Note the both-directions case: moving H's leftover out and S's leftover in at the same time is fine, because each leftover is money its own type could not use in pass 1.

### How FR12 uses it (reference for the FR12 owner)

```python
pools = PoolSplit.from_release(release)                                  # initial columns
pass1 = fund_in_rank_order(households, budget=pools.household_pkr) \
      + fund_in_rank_order(shopkeepers, budget=pools.shopkeeper_pkr)
plan = plan_spillover(pools, usage_from_allocations(pass1), enabled=fund.pool_spillover_enabled)
for t in ("HOUSEHOLD", "SHOPKEEPER"):
    if plan.extra_for(t) > 0:
        fund_more_unmet_in_rank_order(pass1, t, budget=plan.extra_for(t) + leftover_after_pass1(t, plan))
record_split_outcome(db, release, pools, plan, usage_from_allocations(pass1), actor="system")
```
`leftover_after_pass1(t, plan)` is the receiver's own pass-1 leftover that was not moved out; FR12 decides whether its pass 2 can use it (recommended: yes, the effective pool is the budget).

### Record (persist + audit)

```python
def record_split_outcome(db, release, pools, plan, final_usage, actor, reason="ALLOCATION_SAVED") -> dict:
    used_total = final_usage.household_used_pkr + final_usage.shopkeeper_used_pkr
    returned = release.amount_pkr - used_total
    checks = [
        pools.household_pkr + pools.shopkeeper_pkr == release.amount_pkr,
        final_usage.household_used_pkr <= plan.effective_household_pkr,
        final_usage.shopkeeper_used_pkr <= plan.effective_shopkeeper_pkr,
        returned >= 0,
    ]
    if not all(checks):
        raise SplitInvariantError(f"split invariants failed for release {release.id}")
    details = {
        **pools.as_json(),                                                 # share, rounding, initial
        "spillover_enabled": plan.enabled,
        "moves": [{"from": m.from_type, "to": m.to_type, "amount_pkr": str(m.amount_pkr)} for m in plan.moves],
        "effective": {"household_pkr": str(plan.effective_household_pkr),
                      "shopkeeper_pkr": str(plan.effective_shopkeeper_pkr)},
        "used": {"household_pkr": str(final_usage.household_used_pkr),
                 "shopkeeper_pkr": str(final_usage.shopkeeper_used_pkr)},
        "unmet": {"household_pkr": str(final_usage.household_unmet_pkr),
                  "shopkeeper_pkr": str(final_usage.shopkeeper_unmet_pkr)},
        "returned_to_fund_pkr": str(returned),
        "status": "FINAL",
        "recorded_at": clock.now().isoformat(),
    }
    release.split_details = details
    audit.record(db, event_type="POOL_SPLIT_RECORDED", entity_type="FundRelease", entity_id=release.id,
                 actor=actor, payload={**details, "release_tier": release.tier, "recorded_reason": reason})
    return details
```

Money never "moves back" in the ledger: `returned_to_fund_pkr` was never reserved by FR17, so `DisasterFund.remaining_pkr` already includes it once the release leaves FR7's earmarked statuses.

## Configuration

| Key | Source | Default |
|---|---|---|
| `fund_defaults.household_share_pct` | `data/seeds/funds.yaml` | 70 |
| `fund_defaults.pool_spillover_enabled` | same | true |

No environment variables.

## Commands

```bash
alembic upgrade head
pytest tests/unit/triggers/test_split_pools.py tests/unit/triggers/test_spillover.py -q
pytest tests/integration/triggers -k split -q
pytest tests/contract/test_split_contract.py -q
```

## Verification checklist

| Scenario | Test / step |
|---|---|
| US1-1 default 70 | `test_split_api.py::test_new_fund_defaults_70` |
| US1-2 save 60 via FR6 | `test_split_api.py::test_put_share_60` |
| US1-3 invalid share | `test_split_pools.py::test_validate_split*`, `test_split_api.py::test_put_invalid_share` |
| US1-4 preview numbers | `test_split_api.py::test_preview_60` |
| US2-1/2/3 pools and rounding | `test_split_pools.py::test_split_pools_cases` |
| US2-4 share change mid-episode | `test_split_outcome.py::test_share_change_applies_to_next_tier` |
| US3-1…4 spillover | `test_spillover.py` matrix + `test_split_outcome.py` |
| US4-1/2 breakdown | `test_split_api.py::test_breakdown_*` |
| SC-001 property | `test_split_pools.py::test_property_exact_sum` |
| SC-003 used + returned | `test_split_outcome.py::test_cross_fr_used_plus_returned` |

## Demo notes

On the Releases panel during the Sindh 2022 replay, the presenter clicks the Watch release and shows the pool card: "PKR 5,000,000 → households 3,500,000 / shopkeepers 1,500,000". If the synthetic register has fewer households than the pool covers, the card shows "PKR 900,000 moved to shopkeepers; PKR 600,200 stays in the fund". This supports the "every rupee is accounted for" line for judges.

## Pitfalls

- `isinstance(True, int)` is true in Python; reject bools explicitly in `validate_split`.
- Never use `float` for shares × amounts; `Decimal * int / Decimal(100)` keeps precision.
- Do not overwrite `household_pool_pkr` / `shopkeeper_pool_pkr` after firing; they are the firing record.
- `record_split_outcome` must run in the same transaction as the allocation save, or the audit and the allocations can disagree.
- Unmet need uses `computed_amount_pkr` (what the rule asked for), not `final_amount_pkr` (0 for unmet).
