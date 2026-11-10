# Implement Guide: FR6 Fund and Tier Configuration

## Execution order

| Step | Tasks | Output |
|---|---|---|
| 1 | T001–T003 | Folders, fixtures, defaults confirmed |
| 2 | T004 → T005 | `tiers.py` merged early (FR3/FR7 unblocked) |
| 3 | T006–T011 (red) → T012–T014 (green) | Valid save path with audit |
| 4 | T015–T017 (red) → T018–T019 (green) | All validation codes |
| 5 | T020 → T021–T022 ‖ T023–T024 → T025–T026 | Copy draft, role scoping, list |
| 6 | T027–T029 | Registry, perf check, lint/type |

## Key algorithms

### Tier mapping (`app/services/triggers/tiers.py`)

```python
TIER_ORDER: tuple[TierName, ...] = ("WATCH", "WARNING", "ACTION")
_RANK = {"NONE": 0, "WATCH": 1, "WARNING": 2, "ACTION": 3}

def tier_rank(level: TierLevel) -> int:
    return _RANK[level]

@dataclass(frozen=True)
class TierConfig:
    rules: tuple[TierRule, TierRule, TierRule]

    def rule(self, name: TierName) -> TierRule:
        return next(r for r in self.rules if r.name == name)

    def cumulative_pct(self, name: TierName) -> int:
        idx = TIER_ORDER.index(name)
        return sum(r.release_pct for r in self.rules[: idx + 1])

    def tiers_up_to(self, level: TierLevel) -> list[TierRule]:
        return [r for r in self.rules if tier_rank(r.name) <= tier_rank(level)]

def tier_for_score(score: int, config: TierConfig) -> TierLevel:
    level: TierLevel = "NONE"
    for rule in config.rules:                  # ordered WATCH → ACTION
        if score >= rule.min_score:
            level = rule.name
    return level

def load_tier_config(db: Session, fund: DisasterFund) -> TierConfig:
    rows = {t.name: t for t in db.scalars(select(TriggerTier).where(TriggerTier.fund_id == fund.id))}
    return TierConfig(tuple(
        TierRule(n, rows[n].min_score, rows[n].release_pct, rows[n].requires_approval) for n in TIER_ORDER
    ))  # type: ignore[arg-type]
```

### Validation (`app/services/triggers/validation.py`)

```python
MAX_PKR = Decimal("999999999999.99")

def _money_ok(v: Decimal) -> bool:
    return v >= 0 and v <= MAX_PKR and v.as_tuple().exponent >= -2

def validate_fund_settings(body: FundSettingsIn, fund: DisasterFund | None) -> list[FieldError]:
    errs: list[FieldError] = []
    if any(t.requires_approval is not None for t in body.tiers):
        errs.append(FieldError("tiers[].requires_approval", "REQUIRES_APPROVAL_LOCKED",
                               "Approval rules are fixed: Action always needs the DC."))
    names = [t.name for t in body.tiers]
    if sorted(names) != sorted(TIER_ORDER):
        errs.append(FieldError("tiers", "TIER_SET_INVALID", "Give exactly one WATCH, WARNING and ACTION tier."))
        return errs                              # later tier checks need a valid set
    by = {t.name: t for t in body.tiers}
    scores = [by[n].min_score for n in TIER_ORDER]
    pcts = [by[n].release_pct for n in TIER_ORDER]
    if any(not 1 <= s <= 100 for s in scores):
        errs.append(FieldError("tiers[].min_score", "TIER_THRESHOLD_RANGE_INVALID", "Thresholds must be 1 to 100."))
    elif not scores[0] < scores[1] < scores[2]:
        errs.append(FieldError("tiers[].min_score", "TIER_THRESHOLD_ORDER_INVALID",
                               "Thresholds must increase: Watch < Warning < Action."))
    if any(not 0 <= p <= 100 for p in pcts):
        errs.append(FieldError("tiers[].release_pct", "TIER_PCT_RANGE_INVALID", "Each increment must be 0 to 100."))
    elif sum(pcts) != 100:
        errs.append(FieldError("tiers[].release_pct", "TIER_PCT_SUM_INVALID",
                               f"Release increments add up to {sum(pcts)}; they must add up to 100."))
    if not _money_ok(body.allocated_pkr):
        errs.append(FieldError("allocated_pkr", "FUND_AMOUNT_INVALID", "Fund amount must be 0 or more, max 2 decimals."))
    elif fund and body.allocated_pkr < fund.reserved_pkr + fund.disbursed_pkr:
        errs.append(FieldError("allocated_pkr", "FUND_BELOW_COMMITTED",
                               f"PKR {fund.reserved_pkr + fund.disbursed_pkr} is already committed."))
    errs += validate_split(body.household_share_pct)          # FR9
    if not _money_ok(body.shopkeeper_cap_pkr):
        errs.append(FieldError("shopkeeper_cap_pkr", "SHOPKEEPER_CAP_INVALID", "Cap must be zero or more."))
    if not 0 <= body.shopkeeper_stock_pct <= 100:
        errs.append(FieldError("shopkeeper_stock_pct", "STOCK_PCT_INVALID", "Stock share must be 0 to 100."))
    h = body.household_amount_by_tier
    if set(h) != set(TIER_ORDER) or any(not _money_ok(v) for v in h.values()):
        errs.append(FieldError("household_amount_by_tier", "HOUSEHOLD_AMOUNTS_INVALID",
                               "Give WATCH, WARNING and ACTION amounts, each 0 or more."))
    if not 1 <= body.event_close_days <= 60:
        errs.append(FieldError("event_close_days", "EVENT_CLOSE_DAYS_INVALID", "Close days must be 1 to 60."))
    return errs
```

### Save (`app/services/triggers/fund_settings.py`)

```python
def update_settings(self, db: Session, district_id: UUID, body: FundSettingsIn, actor: User) -> FundSettingsOut:
    scenario_id = current_scenario()
    fund = db.scalar(select(DisasterFund)
                     .where(DisasterFund.district_id == district_id, scenario_filter(DisasterFund, scenario_id))
                     .with_for_update())
    errors = validate_fund_settings(body, fund)
    if errors:
        raise FundSettingsInvalid(errors)                     # 422, nothing written
    current_version = fund.version if fund else 0
    if body.version != current_version:
        raise Conflict("VERSION_CONFLICT", f"Current version is {current_version}.")

    created = fund is None
    if created:
        fund = create_fund_with_tiers(db, district_id, scenario_id)   # requires_approval: ACTION only
    before = snapshot(fund, db)
    apply(body, fund, db)                                             # fund fields + tier rows
    after = snapshot(fund, db)
    changed = diff_fields(before, after)
    if not changed and not created:
        db.rollback_to_savepoint_if_any()                             # nothing to save
        return to_out(fund, changed_fields=[], audit_seq=None)

    fund.remaining_pkr = fund.allocated_pkr - fund.reserved_pkr - fund.disbursed_pkr
    fund.version = current_version + 1
    entry = audit.record(db, event_type="FUND_SETTINGS_UPDATED", entity_type="DisasterFund",
                         entity_id=fund.id, actor=str(actor.id),
                         payload={"district_id": str(district_id), "created": created,
                                  "version_before": current_version, "version_after": fund.version,
                                  "changed_fields": changed, "before": before, "after": after,
                                  "open_event_id": open_event_id(db, district_id, scenario_id)})
    db.flush()                                                         # caller (request scope) commits
    return to_out(fund, changed_fields=changed, audit_seq=entry.seq)
```

`snapshot()` serialises money with `str(Decimal)` and tiers as `{name: [min_score, release_pct]}`, so the audit payload is canonical and PII-free.

### Copy draft

```python
def build_copy_draft(self, db, target_district_id, source_district_id, include_amount=False) -> FundSettingsDraft:
    if target_district_id == source_district_id:
        raise Unprocessable("COPY_SOURCE_SAME_AS_TARGET")
    src = get_fund(db, source_district_id) or raise_not_found("SOURCE_FUND_NOT_FOUND")
    tgt = get_fund(db, target_district_id)                     # may be None
    draft = settings_in_from(src, load_tier_config(db, src))
    draft.version = tgt.version if tgt else 0
    if not include_amount:
        draft.allocated_pkr = tgt.allocated_pkr if tgt else Decimal("0.00")
    return FundSettingsDraft(**draft.model_dump(), copied_from=CopiedFrom.of(src))
```

## Configuration

| Key | Source | Default |
|---|---|---|
| `fund_defaults.thresholds` | `data/seeds/funds.yaml` | WATCH 40, WARNING 60, ACTION 80 |
| `fund_defaults.increments` | same | 10 / 30 / 60 |
| `fund_defaults.household_share_pct` | same | 70 |
| `fund_defaults.shopkeeper_cap_pkr` | same | 50000.00 |
| `fund_defaults.shopkeeper_stock_pct` | same | 40 |
| `fund_defaults.household_amount_by_tier` | same | 3000 / 7000 / 15000 |
| `fund_defaults.event_close_days` | same | 14 |
| `fund_defaults.pool_spillover_enabled` | same | true |

No environment variables are added.

## Commands

```bash
pytest tests/unit/triggers/test_tier_for_score.py tests/unit/triggers/test_fund_settings_validation.py -q
pytest tests/integration/triggers -k fund_settings -q
pytest tests/contract/test_fund_settings_contract.py -q
pytest -m perf tests/integration/triggers/test_fund_settings_api.py
ruff check app/services/triggers app/api/v1/settings.py && mypy --strict app/services/triggers
```

## Verification checklist

| Scenario | Test / step |
|---|---|
| US1-1 valid save, version +1, 1 audit | `test_fund_settings_api.py::test_valid_put_saves`, `test_fund_settings_audit.py::test_one_entry_per_save` |
| US1-2 reload shows values + derived 30 | `test_fund_settings_contract.py::test_get_shape` |
| US1-3 next run uses new values | FR7 `test_engine.py::test_settings_change_applies_next_run` (cross-FR) |
| US1-4 no-op | `test_fund_settings_audit.py::test_noop_writes_nothing` |
| US1-5 requires_approval locked | `test_fund_settings_api.py::test_requires_approval_locked` |
| US2-1…5 validation codes | `test_fund_settings_validation.py` (parametrised) + `test_invalid_put_saves_nothing` |
| US2-6 version conflict | `test_fund_settings_api.py::test_version_conflict` |
| US3-1…3 copy draft | `test_fund_settings_api.py::test_copy_draft*` |
| US4-1…4 role matrix, list | `test_fund_settings_api.py::test_role_matrix`, `test_list_settings` |
| Atomic with audit | `test_fund_settings_audit.py::test_audit_failure_rolls_back` |

## Demo notes

Before the replay, the presenter shows Dadu's Fund settings (PKR 50 M, 40/60/80, 10/30/60, 70/30, cap 50 000) for about 10 seconds. Optionally they try increments 10/30/50 to show the instant validation error and that nothing saved. Those edits happen under the replay scenario header so live data is untouched.

## Pitfalls

- Do not compare floats; increments and thresholds are ints, money is `Decimal`. Parse money from strings in the schema (`Decimal`, not `float`).
- Lock the fund row (`FOR UPDATE`) before diffing, or two concurrent saves could both pass the version check.
- Never let `apply()` touch `requires_approval` on existing rows.
- `remaining_pkr` must be recomputed after `allocated_pkr` changes, otherwise FR17's constraint test fails.
- The no-op path must not call `audit.record`. FR20's chain would otherwise contain meaningless entries.
- Scenario filter: always `scenario_id IS NULL` for live. Use the foundation helper, not a hand-written `== None`.
