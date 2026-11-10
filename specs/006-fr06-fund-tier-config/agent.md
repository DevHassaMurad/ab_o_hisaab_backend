# Agent Brief: FR6 Fund and Tier Configuration

## Owner role
Backend & alerts

## Mission
Let the PDMA officer set each district's anticipatory fund: the amount in PKR, the three tier thresholds (40/60/80), the release increments (10/30/60) and the related fund rules (split, shopkeeper cap, household amounts, event close days). Settings are validated as one unit, saved atomically with one audit entry, and apply from the next daily run. This is the configuration every trigger, release and allocation reads (Principle IX).

## Scope
**In (MVP)**
- Read, update and list fund settings per district (UC01 main flow).
- Validation: increments sum to 100, thresholds strictly increase within 1–100, money and range checks (UC01-5). A failed save writes nothing (UC01-5a).
- Copy settings from another district into an unsaved draft (UC01-1a).
- Optimistic locking with `DisasterFund.version`.
- One `FUND_SETTINGS_UPDATED` audit entry per save, in the same transaction (UC01-6).
- Shared tier helpers: `TierConfig`, `load_tier_config()`, `tier_for_score()`. FR3 and FR7 use them.
- `requires_approval` stays locked: no schema accepts it.

**Out**
- Pool split maths and spillover (FR9 `009-fr09-beneficiary-split`). FR6 calls FR9's `validate_split()`.
- Meaning of household amounts and the stock percentage (FR12 `012-fr12-amount-rules`). FR6 only checks their shape and ranges.
- Firing releases (FR7), approvals (FR8).
- Province scoping for officers, the PDMA Director General role (roadmap, D6).
- A settings history UI: use FR20 `GET /api/v1/audit?entity_type=DisasterFund`.

## Read first
- `specs/_authoring-guide.md`
- `.specify/memory/constitution.md` (Principles II, III, IX; D4, D5)
- `specs/000-foundation/data-model.md` §4 (DisasterFund, TriggerTier)
- `specs/000-foundation/plan.md` (interfaces, API conventions, seed users)
- `specs/006-fr06-fund-tier-config/spec.md`, `plan.md`, `clarify.md`
- `specs/009-fr09-beneficiary-split/plan.md` (`validate_split`)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, auth (`require_role`), `ScenarioContext`, `Clock`, problem-JSON errors, seeds |
| Depends on | FR20 `020-fr20-audit-log` | `AuditService.record` (foundation stub is fine until FR20 lands) |
| Depends on | FR9 | `validate_split()`; until merged, FR6 uses an inline 0–100 integer check with the same error code |
| Blocks | FR3 | `tier_for_score()` maps a score to `RiskIndex.tier` |
| Blocks | FR7 | `TierConfig` (thresholds, increments, `requires_approval`) |
| Blocks | FR9, FR12 | They read the fund fields FR6 writes |
| Blocks | FR5 | Replay clones live fund settings into each scenario |

## Interfaces
**Consumes**: `AuditService.record(db, *, event_type, entity_type, entity_id, actor, payload)`, `Clock.now()`, `current_scenario()`, `require_role(...)`, FR9 `validate_split(household_share_pct) -> list[FieldError]`.

**Provides**
- `FundSettingsService.get_settings(db, district_id) -> FundSettingsOut`
- `FundSettingsService.update_settings(db, district_id, body: FundSettingsIn, actor: User) -> FundSettingsOut`
- `FundSettingsService.build_copy_draft(db, target_district_id, source_district_id, include_amount: bool) -> FundSettingsIn`
- `load_tier_config(db, fund) -> TierConfig` and `tier_for_score(score: int, config: TierConfig) -> TierLevel`
- `validate_fund_settings(body, fund) -> list[FieldError]`
- HTTP: `GET/PUT /api/v1/districts/{district_id}/fund-settings`, `GET /api/v1/districts/{district_id}/fund-settings/draft`, `GET /api/v1/fund-settings`

## Files you own
- `app/api/v1/settings.py`
- `app/schemas/fund_settings.py`
- `app/services/triggers/__init__.py`
- `app/services/triggers/tiers.py`
- `app/services/triggers/validation.py`
- `app/services/triggers/fund_settings.py`
- `tests/unit/triggers/test_tier_for_score.py`
- `tests/unit/triggers/test_fund_settings_validation.py`
- `tests/integration/triggers/conftest.py` (fund and tier fixtures shared by FR6–FR9)
- `tests/integration/triggers/test_fund_settings_api.py`
- `tests/integration/triggers/test_fund_settings_audit.py`
- `tests/contract/test_fund_settings_contract.py`

FR9 adds one route (`/fund-settings/split-preview`) to `app/api/v1/settings.py`. Coordinate; nothing else in that file belongs to FR9.

## Guardrails
| Principle | How it applies |
|---|---|
| II Human authority | `requires_approval` is read-only. Any request body that carries it gets 422 `REQUIRES_APPROVAL_LOCKED`. A test proves Action stays `true`. |
| III Traceability | Every successful save writes exactly one audit entry in the same transaction. A no-op save writes none. |
| VI Privacy | DC reads only their own district. Donors get 403. No PII is involved. |
| IX Config over code | Defaults come from `data/seeds/funds.yaml`. No numbers in code apart from validation bounds. |
| XI Tests | Validation and tier mapping feed the money path: tests first. |

## Definition of Done
- [ ] All tasks in `tasks.md` checked; tests written first and seen failing.
- [ ] Every UC01 acceptance scenario in `spec.md` has a passing test.
- [ ] Each validation error code has a parametrised test, and a failed save leaves the DB and audit chain unchanged.
- [ ] `tier_for_score` boundary tests pass (39/40/59/60/79/80/100).
- [ ] Contract test: response schemas match plan.md, money is a string decimal, `requires_approval` cannot be written.
- [ ] `ruff` and `mypy --strict app/services/triggers` are clean.
- [ ] `GET` p95 < 500 ms on the seeded pilot (68 districts).

## Hand-off notes
- FR3 and FR7 must import `tier_for_score` from `app/services/triggers/tiers.py`. Do not duplicate threshold logic.
- FR7 reads increments through `TierConfig.cumulative_pct(tier)` (D4).
- FR5 clones `DisasterFund` + `TriggerTier` rows per scenario. FR6 writes to whichever fund matches the active `ScenarioContext`.
- FR12 may add stricter checks for `household_amount_by_tier` by extending `validate_fund_settings`. Keep the error codes listed in plan.md.
