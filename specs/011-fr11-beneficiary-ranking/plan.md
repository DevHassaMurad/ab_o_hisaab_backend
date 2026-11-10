# Implementation Plan: FR11 Beneficiary Ranking

**Branch**: `011-fr11-beneficiary-ranking` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-fr11-beneficiary-ranking/spec.md`

## Summary

FR11 implements the orchestration of `AllocationService.allocate(db, fund_release)` and everything about **who comes first**: eligibility, normalised factors, the `rank-v1` priority score, deterministic tie-breaks, EN/UR reasons, the `fair-v1` fairness check, officer overrides, and `finalize` (the only path from a ranked list to payment reservation). FR12 supplies **how much**: per-beneficiary target and top-up, rank-order fill with `UNMET_NEED`, spillover and re-fill. Both live in `app/services/allocation/`.

### Ownership split of `AllocationService.allocate`

| Step inside `allocate()` | Owner | Function |
|---|---|---|
| Lock release, idempotency check, build context | FR11 | `service.allocate`, `context.build_context` |
| Eligible beneficiaries | FR11 | `ranking.load_eligible` |
| Target amount, prior event amount, top-up, amount formula | **FR12** | `amounts.compute_targets` |
| Drop already-covered (top-up 0) | FR11 | `service.allocate` |
| Factors, score, sort, rank | FR11 | `ranking.rank` |
| Fill pools in rank order, spillover, `FUNDED`/`UNMET_NEED` | **FR12** | `fill.fill_release` |
| Reasons EN/UR | FR11 (uses FR12's amount-basis text) | `reasons.build_reasons` |
| Persist rows | FR11 | `service._persist` |
| Fairness check | FR11 | `fairness.run_fairness_check` |
| Audit `ALLOCATION_COMPUTED` | FR11 | `service.allocate` |
| Auto-finalise Watch/Warning when fairness passes | FR11 | `service.finalize` |
| Override limits and re-fill | **FR12** | `amounts.override_limit`, `fill.refill_release` |
| Summary endpoint | **FR12** | `summary.release_summary` |

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x; stdlib `decimal`, `statistics`, `hashlib`, `json`

**Storage**: PostgreSQL 16; tables `allocation`, `fairness_check`, `fund_release`, `disaster_fund` (foundation) + migration 0011

**Testing**: pytest, factory-boy, httpx TestClient; seeded property tests (stdlib `random.Random(seed)`)

**Target Platform**: Linux container

**Project Type**: web-service

**Performance Goals**: `allocate` < 10 s for 10 000 eligible; list p95 < 500 ms

**Constraints**: deterministic; money `Decimal`; scores stored as float with 2 dp; same transaction for writes and audit; scenario-scoped

**Scale/Scope**: ≤ 10 000 eligible per release; ≤ 3 releases per event

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| I Anticipate | Watch/Warning auto-saved when fairness passes; no review hold | ✅ |
| II Human authority | `finalize` checks `Approval.decision == APPROVED` for `ACTION`; test `test_finalize_gate.py` written first | ✅ |
| III Traceability | Seven audit event types, each in the writing transaction | ✅ |
| IV Explainable | `score_inputs` (raw, factors, weights, contributions, imputed, rules_version, tie-break); reasons from templates | ✅ |
| V Fair & transparent | Published rules endpoint; deterministic sort; override reason required; fairness check gate | ✅ |
| VI Privacy | Donor 403; DC own district; reasons and audit free of phone/CNIC | ✅ |
| VII Degrade gracefully | Missing tehsil risk → district risk, flagged; missing poverty → imputed, flagged | ✅ |
| VIII Honest prototype | `simulated` from beneficiary and release echoed in responses; replay ack labelled simulated | ✅ |
| IX Config over code | Weights, norms, thresholds in settings + per-district JSONB | ✅ |
| XI Tests first | Ranking, fairness, override, approval gate tests precede code | ✅ |
| Fairness hold vs Principle I | A failed check can delay a Watch/Warning release | ⚠️ see Complexity Tracking |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Linear weighted sum with a support penalty, clamped 0–100 | Easy to publish and explain; reproducible by hand | ML ranking (opaque, no labels); multiplicative indices (harder to explain) |
| Sort on the stored 2-dp score, then visible tie-breaks, then `register_ref` | Displayed order always matches displayed values; total order | Hidden float precision; UUID tie-break (not human-checkable); random lottery (not reproducible across replays) |
| Separate ranks per type | Separate pools (FR9) and different factors | Single merged rank (incomparable scores) |
| Fairness at tehsil level within the release, population × risk × vulnerability | Allocation cannot move money across district funds; population exposes sparse registers | Registered-count share (hides the problem); Gini (hard to act on) |
| Hold on failed fairness; auto-ack only in replay | Constitution V requires the officer to see it before saving | Warn after saving (violates V) |
| Pinned overrides + full re-fill | Deterministic, explains the cascade | Manual per-row status changes (error-prone) |
| `finalize` as the single doorway to FR17 | One place enforces Principle II and the fairness gate | Each caller (FR7, FR8, router) reserving payments itself |

## Data model slice

| Entity | Fields | Access |
|---|---|---|
| `FundRelease` | `id`, `event_id`, `fund_id`, `tier`, `household_pool_pkr`, `shopkeeper_pool_pkr`, `risk_index_id`, `status`, `simulated`, `scenario_id` (+ proposed `allocation_saved_at`, `allocation_saved_by`) | read; FR11 writes `status=ALLOCATING` for Watch/Warning and the proposed fields |
| `Allocation` | `rank`, `priority_score`, `score_inputs`, `reason_en`, `reason_ur`, `override_reason`, `overridden_by` (+ proposed `overridden_at`, `scenario_id`) | write |
| `FairnessCheck` | `fund_release_id`, `passed`, `metrics`, `acknowledged_by` (+ proposed `acknowledged_at`, `acknowledgement_note`, `scenario_id`) | write |
| `DisasterFund` | `district_id` (+ proposed `ranking_weights`) | read/write weights |
| `Approval` | `fund_release_id`, `decision` | read |
| `RiskIndex` | `district_id`, `tehsil_id`, `run_date`, `score`, `peak_lead_day`, `tier`, `scenario_id` | read |
| `Beneficiary`, `Tehsil`, `District` | via FR10 | read |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Field | Type | Notes |
|---|---|---|---|
| `DisasterFund` | `ranking_weights` | JSONB, nullable | Per-district override of `RANKING_DEFAULT_WEIGHTS`; null = defaults |
| `FundRelease` | `allocation_saved_at` | timestamptz, nullable | Null = draft list; set = locked, payments may be reserved |
| `FundRelease` | `allocation_saved_by` | str, nullable | User id or `system` / `replay-sim` |
| `FairnessCheck` | `acknowledged_at` | timestamptz, nullable | Save allowed when `passed` or this is set |
| `FairnessCheck` | `acknowledgement_note` | text, nullable | Required with acknowledgement |
| `FairnessCheck` | `scenario_id` | FK, nullable | Shared rule 2 |
| `FairnessCheck` | *unique* `fund_release_id` | | One current check per release (replaced on re-run) |
| `Allocation` | `overridden_at` | timestamptz, nullable | |
| `Allocation` | `scenario_id` | FK, nullable | Shared rule 2; equals the release's |

FR12 proposes the `EXCLUDED` value for `Allocation.status`.

## `score_inputs` shape (`rank-v1`)

```json
{
  "method": "rank-v1",
  "rules_version": "3f9a1c07b2de",
  "type": "HOUSEHOLD",
  "raw": {"poverty_score": 0.9, "family_size": 8, "flood_exposure": 0.8, "existing_support_pkr": "0.00",
          "tehsil_risk_score": 85, "peak_lead_day": 1, "urgency_source": "TEHSIL", "risk_index_id": "…"},
  "factors": {"poverty": 0.9, "family_size": 0.636364, "exposure": 0.8, "urgency": 0.85, "support": 0.0},
  "weights": {"poverty": 0.40, "family_size": 0.20, "exposure": 0.30, "urgency": 0.10, "support_penalty": 0.25},
  "contributions": {"poverty": 36.0, "family_size": 12.73, "exposure": 24.0, "urgency": 8.5, "support": -0.0},
  "imputed": [],
  "tie_break": [0.9, 0.8, 8, "0.00", "SYN-PK60501-H-00042"],
  "tehsil_scope": "AFFECTED",
  "amount": {"…": "set by FR12"},
  "fill": {"…": "set by FR12"}
}
```

`rules_version` = first 12 hex chars of `sha256(canonical_json(weights + norms))`.

## Fairness `metrics` shape (`fair-v1`)

```json
{
  "method": "fair-v1",
  "thresholds": {"min_ratio": 0.5, "min_expected_share": 0.05, "sparse_relative_coverage": 0.5, "min_completeness": 0.7},
  "total_funded_pkr": "2100000.00",
  "tehsils": [
    {"tehsil_id": "…", "pcode": "PK60501", "population": 520000, "risk": 0.85, "expected_share": 0.4121,
     "funded_pkr": "1260000.00", "actual_share": 0.6, "ratio": 1.456, "relative_coverage": 1.04, "completeness": 0.96,
     "sparse": false, "flag": null},
    {"tehsil_id": "…", "pcode": "PK60504", "population": 310000, "risk": 0.9, "expected_share": 0.2602,
     "funded_pkr": "84000.00", "actual_share": 0.04, "ratio": 0.154, "relative_coverage": 0.21, "completeness": 0.88,
     "sparse": true, "flag": "SPARSE_UNDERFUNDED"}
  ],
  "notes": [],
  "min_ratio_observed": 0.154,
  "flags": 1,
  "acknowledged_simulated": false
}
```

## API contracts

Tag: **Beneficiaries**. Router `app/api/v1/allocations.py`. All under `/api/v1`. Problem JSON errors with `code`.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/fund-releases/{release_id}/allocations` | PDMA_OFFICER, ADMIN, DC (own district) | Ranked list; filters `type`, `status`, `tehsil_id`; `include=score_inputs` |
| GET | `/allocations/{allocation_id}` | PDMA_OFFICER, ADMIN, DC (own district) | One row with `score_inputs` |
| PATCH | `/allocations/{allocation_id}/override` | PDMA_OFFICER | Change amount with reason |
| DELETE | `/allocations/{allocation_id}/override` | PDMA_OFFICER | Reset to rule amount |
| GET | `/fund-releases/{release_id}/fairness` | PDMA_OFFICER, ADMIN, DC (own district) | Fairness result |
| POST | `/fund-releases/{release_id}/allocations/save` | PDMA_OFFICER | Acknowledge (if needed) and save |
| POST | `/fund-releases/{release_id}/allocations/recompute` | PDMA_OFFICER | Rebuild an unsaved list |
| GET | `/allocation-rules?district_id=` | any authenticated role | Published rules (FR12 adds `amounts`) |
| PUT | `/allocation-rules/{district_id}` | PDMA_OFFICER | Update district weights |
| GET | `/fund-releases/{release_id}/allocations/summary` | PDMA_OFFICER, ADMIN, DC | **FR12** (see 012 plan) |

DONOR → 403 `FORBIDDEN_ROLE` on every route except `GET /allocation-rules`. DC outside own district → 404.

**GET `/fund-releases/{release_id}/allocations?type=HOUSEHOLD&status=FUNDED&page=1&page_size=50`**

```json
{
  "items": [
    {
      "id": "a1…",
      "rank": 1,
      "type": "HOUSEHOLD",
      "beneficiary": {"id": "6f1c…", "register_ref": "SYN-PK60501-H-00042", "name": "غلام رسول",
                      "tehsil": {"id": "…", "pcode": "PK60501", "name": "Dadu"}, "simulated": true},
      "priority_score": 81.23,
      "computed_amount_pkr": "7000.00",
      "final_amount_pkr": "7000.00",
      "status": "FUNDED",
      "override_reason": null,
      "overridden_by": null,
      "reason_ur": "دادو کے 1,240 گھرانوں میں آپ کا نمبر 1 ہے۔ …",
      "reason_en": "Rank 1 of 1,240 households in Dadu. Priority 81.23/100 because …",
      "simulated": true
    }
  ],
  "total": 1240, "page": 1, "page_size": 50,
  "release": {"id": "…", "tier": "WARNING", "saved": false, "fairness_passed": true, "simulated": true}
}
```

**PATCH `/allocations/{id}/override`**

```json
{"final_amount_pkr": "0", "reason": "Household relocated to Hyderabad before the season"}
```

Response `200`:

```json
{
  "allocation": {"id": "…", "status": "EXCLUDED", "computed_amount_pkr": "15000.00", "final_amount_pkr": "0.00",
                 "override_reason": "Household relocated to Hyderabad before the season", "overridden_by": "u-officer"},
  "cascade": {"newly_funded": 1, "newly_unmet": 0, "changed_allocation_ids": ["…"]},
  "fairness": {"passed": true, "flags": 0},
  "pool": {"type": "HOUSEHOLD", "pool_pkr": "4200000.00", "funded_pkr": "4200000.00", "unused_pkr": "0.00"}
}
```

Errors: `422 OVERRIDE_REASON_REQUIRED`, `422 REASON_CONTAINS_PII`, `422 AMOUNT_NOT_ROUNDED` (not a multiple of 100), `422 AMOUNT_ABOVE_LIMIT` (detail includes `limit_pkr`), `409 OVERRIDE_EXCEEDS_POOL`, `409 ALLOCATION_LOCKED`, `409 RELEASE_REJECTED`, `403`, `404`.

**POST `/fund-releases/{release_id}/allocations/save`**

```json
{"acknowledge_fairness": true, "note": "Tehsil Johi register is incomplete; field survey ordered. Proceeding to avoid delay."}
```

Response `200`:

```json
{"result": "SAVED", "allocation_saved_at": "2026-08-20T06:12:44Z", "fairness": {"passed": false, "acknowledged_by": "u-officer"},
 "payments_reserved": 1182}
```

Other results: `{"result": "HELD", "reason": "FAIRNESS_ACK_REQUIRED"}` returned with `409`; `409 APPROVAL_REQUIRED`; `409 RELEASE_REJECTED`; `200 {"result": "ALREADY_SAVED"}` (idempotent). `note` required (10–500 chars) when acknowledging.

**GET `/fund-releases/{release_id}/fairness`** → `{"passed": false, "metrics": {…fair-v1…}, "acknowledged_by": null, "acknowledged_at": null, "acknowledgement_note": null}`

**POST `/fund-releases/{release_id}/allocations/recompute`** → `200` list summary; `409 ALLOCATION_LOCKED` if saved.

**GET `/allocation-rules?district_id=…`**

```json
{
  "district_id": "…",
  "rules_version": "3f9a1c07b2de",
  "method": "rank-v1",
  "household": {"formula": "100 × clamp(0.40·poverty + 0.20·family + 0.30·exposure + 0.10·urgency − 0.25·support, 0, 1)",
                "weights": {"poverty": 0.40, "family_size": 0.20, "exposure": 0.30, "urgency": 0.10, "support_penalty": 0.25},
                "tie_break": ["priority_score desc", "poverty desc", "exposure desc", "family_size desc", "existing_support asc", "register_ref asc"]},
  "shopkeeper": {"formula": "100 × clamp(0.40·stock + 0.30·exposure + 0.30·urgency − 0.25·support, 0, 1)",
                 "weights": {"stock_at_risk": 0.40, "exposure": 0.30, "urgency": 0.30, "support_penalty": 0.25},
                 "tie_break": ["priority_score desc", "exposure desc", "urgency desc", "existing_support asc", "stock_value desc", "register_ref asc"]},
  "norms": {"family_size_cap": 12, "stock_norm_pkr": "250000", "support_norm_pkr": "50000", "urgency_lead_decay": 0.05},
  "fairness": {"method": "fair-v1", "min_ratio": 0.5, "min_expected_share": 0.05, "sparse_relative_coverage": 0.5, "min_completeness": 0.7},
  "amounts": {"…": "FR12 AmountRulesOut"},
  "source": "DEFAULT"
}
```

**PUT `/allocation-rules/{district_id}`** body: `{"household": {...weights}, "shopkeeper": {...weights}}` (norms optional). `200` returns the GET shape with `source: "DISTRICT"`. `422 WEIGHTS_INVALID` with per-field detail. Send `{"reset": true}` to return to defaults.

## Audit events

| event_type | entity | payload |
|---|---|---|
| `ALLOCATION_COMPUTED` | FundRelease | `tier`, `rules_version`, counts by type × status, `already_covered` (`{"HOUSEHOLD": n, "SHOPKEEPER": m}`, read by FR12's summary), totals funded/unmet per type, `fairness_passed`, `list_sha256` (sha256 of canonical `[[beneficiary_id, rank, final, status], …]`), `tehsil_scope` |
| `ALLOCATION_OVERRIDDEN` | Allocation | `fund_release_id`, `beneficiary_id`, `computed_pkr`, `previous_final_pkr`, `final_pkr`, `reason`, `cascade` counts, new `list_sha256` |
| `ALLOCATION_OVERRIDE_RESET` | Allocation | same minus reason |
| `ALLOCATION_RECOMPUTED` | FundRelease | old and new `list_sha256`, `rules_version`, dropped/added counts |
| `FAIRNESS_ACKNOWLEDGED` | FairnessCheck | `fund_release_id`, `flags`, `note`, `simulated` |
| `ALLOCATION_SAVED` | FundRelease | `list_sha256`, funded totals, `saved_by`, `fairness_passed`, `acknowledged` |
| `RANKING_RULES_UPDATED` | DisasterFund | `old`, `new`, `rules_version` |

## Configuration

| Key | Default |
|---|---|
| `RANKING_DEFAULT_WEIGHTS` | `{"household":{"poverty":0.40,"family_size":0.20,"exposure":0.30,"urgency":0.10,"support_penalty":0.25},"shopkeeper":{"stock_at_risk":0.40,"exposure":0.30,"urgency":0.30,"support_penalty":0.25}}` |
| `RANKING_NORMS` | `{"family_size_cap":12,"stock_norm_pkr":250000,"support_norm_pkr":50000,"urgency_lead_decay":0.05}` |
| `FAIRNESS_THRESHOLDS` | `{"min_ratio":0.5,"min_expected_share":0.05,"sparse_relative_coverage":0.5,"min_completeness":0.7}` |
| `ALLOCATION_TEHSIL_SCOPE` | `AFFECTED` (alt `DISTRICT`) |
| `OVERRIDE_REASON_MIN_CHARS` / `_MAX_CHARS` | `10` / `500` |
| `REPLAY_AUTO_ACK_FAIRNESS` | `true` |

## Scheduled jobs

None. `allocate` is called synchronously by FR7 inside the daily pipeline transaction for each new release.

## Project Structure

### Documentation (this feature)

```text
specs/011-fr11-beneficiary-ranking/
├── agent.md
├── spec.md
├── clarify.md
├── plan.md
├── tasks.md
└── implement.md
```

### Source Code (repository root)

```text
app/
├── api/v1/allocations.py                 # FR11 routes (+ FR12 summary route, marked section)
├── schemas/allocations.py                # AllocationOut, AllocationDetailOut, OverrideIn/Out, SaveIn/Out, FairnessOut, RulesOut, RulesUpdateIn
└── services/
    ├── allocation/
    │   ├── service.py                    # AllocationServiceImpl: allocate, finalize, override, reset_override, recompute
    │   ├── context.py                    # AllocationContext, build_context
    │   ├── rules.py                      # RankingWeights, Norms, get_rules(fund), rules_version
    │   ├── ranking.py                    # Candidate, load_eligible, normalise_household/shopkeeper, priority_score, rank
    │   ├── fairness.py                   # run_fairness_check, FairnessResult
    │   ├── overrides.py                  # validate_override_reason
    │   ├── reasons.py                    # build_reasons
    │   ├── amounts.py, fill.py, summary.py   # FR12
    └── explain/allocation_templates.py   # render_allocation_reason (EN/UR)
alembic/versions/0011_fr11_ranking_fairness.py
tests/unit/allocation/{test_normalise,test_ranking,test_fairness,test_reasons}.py
tests/integration/allocation/{test_allocate_flow,test_override,test_finalize_gate}.py
tests/contract/{test_allocations_api,test_allocation_rules_api}.py
```

**Structure Decision**: single project, constitution layout. `AllocationServiceImpl` is bound to the `AllocationService` Protocol in the foundation's service registry (replacing the no-op stub).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Fairness hold can delay a Watch/Warning release (Principle I latency) | Principle V requires the officer to see a failed check before saving | Auto-saving and warning afterwards breaks Principle V; the hold only happens when sparse data actually skews the list, and the officer is alerted immediately |
