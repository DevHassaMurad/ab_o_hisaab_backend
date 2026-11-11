# Implementation Plan: FR19 Donor report

**Branch**: `019-fr19-donor-report` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/019-fr19-donor-report/spec.md`

## Summary

A read-only reporting service computes the spec's metrics with a handful of grouped SQL queries over funds, releases, allocations, payments and latest confirmations, scoped to the donor's linked districts (live or replay). Results go through a suppression pass and into Pydantic models that all inherit `DonorScope`. A `StoryProvider` serves curated (simulated) stories in the MVP. A ReportLab renderer turns the same summary object into a PDF with the FR20 verify result and a SHA-256 fingerprint, and records `DONOR_REPORT_GENERATED`. Three contract tests (schema, route sweep, canary data) prove NFR8.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, **reportlab** (new, pure Python), PyYAML (stories; already used by seeds)

**Storage**: PostgreSQL 16 (read-only queries) + `data/seeds/donor_stories.yaml`

**Testing**: pytest, httpx TestClient, foundation factories; PDF tests render with `pageCompression=0` so bytes can be scanned

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: summary p95 < 500 ms at 50 000 payments (NFR1); PDF < 5 s

**Constraints**: aggregates only (NFR8); Decimal money; every response labelled `simulated` when applicable; no writes except the audit entry on PDF generation

**Scale/Scope**: a donor linked to 1–10 district funds; pilot ≤ 68 districts

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| II Human authority | Donor endpoints are GET only; donors cannot change anything | ✅ |
| III Traceability | PDF generation audited with `report_sha256` and chain head; verify link to FR20 | ✅ |
| VI Privacy by role | All schemas inherit `DonorScope`; extended deny-list; small-cell suppression; route sweep + canary tests | ✅ |
| VII Degrade gracefully | `ledger_consistent` flag; FR15 missing → received 0 with explanation | ✅ |
| VIII Honest prototype | `simulated` field, PDF banner, curated stories labelled simulated | ✅ |
| IX Config over code | `DONOR_MIN_CELL_SIZE`, story file path, counted statuses in settings/constants; no district names in code | ✅ |
| X Simplicity | Plain SQL aggregates; no warehouse, no cache | ✅ |
| XI Test the money paths | Metric fixture tests and donor no-PII contract tests written first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Compute metrics from rows and cross-check against the fund ledger | Independent check of FR17's ledger; catches drift | Ledger only (no check); rows only (ignores the ledger) |
| Latest confirmation via `DISTINCT ON (payment_id) … ORDER BY received_at DESC` | One indexed pass, Postgres-native | Window function subquery (same cost, more verbose) |
| ReportLab for the PDF | Pure Python, Windows-friendly | WeasyPrint (GTK/Pango native deps on Windows) |
| PDF built from `DonorSummaryOut`, never from raw rows | The PDF cannot leak more than the JSON | Separate PDF queries |
| Suppression applied after aggregation in Python | Simple, testable, k configurable | SQL `HAVING` (loses secondary suppression) |
| Route sweep test using `require_role.__roles__` | Any new donor-reachable route must be reviewed | Manual review only |
| Curated stories in YAML with a validating loader | Honest, reviewable, no DB entity needed | DB table (would be a new entity; not allowed) |

## Data model slice

| Entity (data-model.md) | Fields | R/W |
|---|---|---|
| `DonorContribution` §8 | `donor_id`, `fund_id`, `amount_pkr` | R |
| `DisasterFund` §4 | `id`, `district_id`, `allocated_pkr`, `reserved_pkr`, `disbursed_pkr`, `remaining_pkr`, `scenario_id` | R |
| `District` §1 | `id`, `pcode`, `name` | R |
| `FundRelease` §4 | `fund_id`, `tier`, `amount_pkr`, `household_pool_pkr`, `shopkeeper_pool_pkr`, `status`, `simulated`, `scenario_id` | R |
| `Allocation` §5 | `fund_release_id`, `beneficiary_id` (join only), `computed_amount_pkr`, `status` | R |
| `Beneficiary` §5 | `id` (join only), `type` | R |
| `Payment` §6 | `fund_release_id`*, `district_id`*, `beneficiary_id` (join/distinct only), `amount_pkr`, `status`, `is_stuck`, `simulated`, `scenario_id` | R |
| `Confirmation` §7 | `payment_id`, `status`, `received_at` | R |
| `OutcomeReport` §5 | `event_id`, `answers` (aggregated only) | R (flag `FR22_OUTCOME`) |
| `AuditLog` §8 | via `verify_chain`, `AuditService.record` | R/W |

\* Proposed by `017-fr17-payment-tracking`.

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

None. FR19 relies on `Payment.district_id` and `Payment.fund_release_id` proposed by FR17. (Observation for the foundation owner: `Confirmation` has no `scenario_id`; FR19 scopes confirmations through their payment, so it is not blocked, but shared rule 2 suggests adding it — `015-fr15-receipt-confirmation` should propose it.)

## Metric SQL (normative sketch; `:fids` = fund ids in scope)

```sql
-- releases
SELECT r.fund_id, r.tier,
       SUM(r.amount_pkr)          FILTER (WHERE r.status IN ('APPROVED','ALLOCATING','RELEASED','PARTIALLY_RELEASED')) AS released,
       SUM(r.household_pool_pkr)  FILTER (WHERE r.status IN (...counted...)) AS hh_pool,
       SUM(r.shopkeeper_pool_pkr) FILTER (WHERE r.status IN (...counted...)) AS sk_pool,
       SUM(r.amount_pkr)          FILTER (WHERE r.status = 'PENDING_APPROVAL') AS awaiting_approval
FROM fund_release r WHERE r.fund_id = ANY(:fids) GROUP BY r.fund_id, r.tier;

-- payments with latest confirmation
WITH lc AS (
  SELECT DISTINCT ON (c.payment_id) c.payment_id, c.status
  FROM confirmation c JOIN payment p ON p.id = c.payment_id
  WHERE p.fund_release_id IN (SELECT id FROM fund_release WHERE fund_id = ANY(:fids))
    AND c.status <> 'UNPARSEABLE'
  ORDER BY c.payment_id, c.received_at DESC
)
SELECT p.district_id, b.type, r.tier,
       SUM(p.amount_pkr) FILTER (WHERE p.status = 'PAID')                                  AS disbursed,
       SUM(p.amount_pkr) FILTER (WHERE p.status = 'PAID' AND lc.status = 'CONFIRMED')      AS received,
       SUM(p.amount_pkr) FILTER (WHERE p.status = 'PAID' AND lc.status = 'NOT_RECEIVED')   AS not_received,
       SUM(p.amount_pkr) FILTER (WHERE p.status IN ('RESERVED','PROCESSING','FAILED'))     AS in_flight,
       COUNT(DISTINCT p.beneficiary_id) FILTER (WHERE p.status = 'PAID')                   AS reached,
       COUNT(DISTINCT p.beneficiary_id) FILTER (WHERE p.status = 'PAID' AND lc.status = 'CONFIRMED') AS confirmed_people
FROM payment p
JOIN fund_release r ON r.id = p.fund_release_id
JOIN beneficiary b ON b.id = p.beneficiary_id
LEFT JOIN lc ON lc.payment_id = p.id
WHERE r.fund_id = ANY(:fids)
GROUP BY GROUPING SETS ((), (p.district_id), (b.type), (p.district_id, b.type), (r.tier));

-- unmet need (counted releases only)
SELECT r.fund_id, b.type, SUM(a.computed_amount_pkr), COUNT(*)
FROM allocation a JOIN fund_release r ON r.id = a.fund_release_id JOIN beneficiary b ON b.id = a.beneficiary_id
WHERE r.fund_id = ANY(:fids) AND a.status = 'UNMET_NEED' AND r.status IN (...counted...)
GROUP BY GROUPING SETS ((r.fund_id), (b.type), (r.fund_id, b.type));
```

`COUNT(DISTINCT beneficiary_id)` is computed per grouping set, so a person paid in two tiers counts once per cell. NULL sums are coalesced to `0.00` in Python.

## API contracts

Base `/api/v1`, tag `Reporting`, router `app/api/v1/reports.py`. `X-Scenario-Id` selects a replay. All responses are `DonorScope` models. Money as string decimals.

### `GET /reports/donor/summary` (`DONOR`, `PDMA_OFFICER`, `ADMIN`)

Query: `district_id` (optional, must be in scope).

200:
```json
{
  "as_of": "2026-11-10T09:30:00Z",
  "scope": {"districts": [{"pcode": "PK605", "name": "Dadu"}, {"pcode": "PK112", "name": "Buner"}],
            "mode": "REPLAY", "scenario_key": "sindh-2022"},
  "contributed_pkr": "20000000.00",
  "totals": {
    "allocated_pkr": "100000000.00", "released_pkr": "40000000.00", "awaiting_approval_pkr": "60000000.00",
    "disbursed_pkr": "31250000.00", "received_pkr": "24100000.00", "not_received_pkr": "350000.00",
    "awaiting_confirmation_pkr": "6800000.00", "in_flight_pkr": "1210000.00", "unused_pkr": "67540000.00",
    "unmet_need_pkr": "2300000.00", "unmet_need_count": 214,
    "beneficiaries_reached": 3120, "beneficiaries_confirmed": 2390,
    "disbursement_rate": 0.781, "confirmation_rate": 0.771
  },
  "by_district": [
    {"pcode": "PK605", "name": "Dadu", "suppressed": false,
     "allocated_pkr": "50000000.00", "released_pkr": "20000000.00", "disbursed_pkr": "15900000.00",
     "received_pkr": "12000000.00", "in_flight_pkr": "610000.00", "unused_pkr": "33490000.00",
     "unmet_need_pkr": "1300000.00", "beneficiaries_reached": 1600,
     "by_type": [
       {"type": "HOUSEHOLD", "suppressed": false, "released_pool_pkr": "14000000.00", "disbursed_pkr": "11200000.00",
        "received_pkr": "8700000.00", "in_flight_pkr": "210000.00", "unmet_need_pkr": "900000.00", "beneficiaries_reached": 1500},
       {"type": "SHOPKEEPER", "suppressed": false, "released_pool_pkr": "6000000.00", "disbursed_pkr": "4700000.00",
        "received_pkr": "3300000.00", "in_flight_pkr": "400000.00", "unmet_need_pkr": "400000.00", "beneficiaries_reached": 100}
     ]}
  ],
  "by_type": [ {"type": "HOUSEHOLD", "...": "..."}, {"type": "SHOPKEEPER", "...": "..."} ],
  "by_tier": [
    {"tier": "WATCH", "status": "RELEASED", "released_pkr": "10000000.00", "disbursed_pkr": "9100000.00", "received_pkr": "7600000.00"},
    {"tier": "WARNING", "status": "PARTIALLY_RELEASED", "released_pkr": "30000000.00", "disbursed_pkr": "22150000.00", "received_pkr": "16500000.00"},
    {"tier": "ACTION", "status": "PENDING_APPROVAL", "released_pkr": "0.00", "disbursed_pkr": "0.00", "received_pkr": "0.00"}
  ],
  "ledger_consistent": true,
  "audit_verify_url": "/api/v1/audit/verify",
  "simulated": true,
  "simulated_note": "Payments are simulated (JazzCash/Easypaisa-shaped). No real money moved."
}
```
Errors: 403 `NO_LINKED_FUNDS`, 403 `FUND_NOT_LINKED`, 403 `ROLE_FORBIDDEN` (DC).

### `GET /reports/donor/payment-counts` (`DONOR`, `PDMA_OFFICER`, `ADMIN`)

```json
{
  "as_of": "2026-11-10T09:30:00Z",
  "totals": {"by_status": {"RESERVED": 40, "PROCESSING": 22, "PAID": 3150, "FAILED": 31, "CANCELLED": 9},
             "amount_by_status_pkr": {"RESERVED": "280000.00", "PROCESSING": "610000.00", "PAID": "31250000.00",
                                      "FAILED": "320000.00", "CANCELLED": "63000.00"},
             "stuck": 2, "total": 3252},
  "by_district": [{"pcode": "PK605", "suppressed": false, "by_status": {"...": 0}, "stuck": 1, "total": 1650}],
  "simulated": true
}
```

### `GET /reports/donor/stories` (`DONOR`, `PDMA_OFFICER`, `ADMIN`)

```json
{
  "items": [{
    "story_key": "dadu-shop-stock-01", "district_pcode": "PK605", "beneficiary_type": "SHOPKEEPER", "tier": "WARNING",
    "title_en": "Stock moved to higher ground", "title_ur": "[Urdu text]",
    "body_en": "A grocery shopkeeper in Johi tehsil used the Warning grant to hire a cart and move rice and flour…",
    "body_ur": "…",
    "source": "CURATED", "label": "Illustrative story (simulated)", "group_size": null, "simulated": true
  }],
  "total": 3, "simulated": true
}
```

### `GET /reports/donor/report.pdf` (`DONOR`, `PDMA_OFFICER`, `ADMIN`)

Query: `district_id` (optional). 200 `application/pdf`, `Content-Disposition: attachment; filename="aab-o-hisaab-donor-report-20261110.pdf"`, header `X-Report-SHA256: <hex>`. Side effect: audit `DONOR_REPORT_GENERATED`:
```json
{"report_sha256": "…", "district_pcodes": ["PK605", "PK112"], "as_of": "…", "scenario_id": "…",
 "chain_key": "replay:…:1", "chain_head_seq": 1312, "chain_head_hash": "…", "chain_valid": true, "requested_by_role": "DONOR"}
```

### Denial routes (any role) → 403 `DONOR_AGGREGATE_ONLY`

`GET /reports/donor/beneficiaries`, `GET /reports/donor/payments`, `GET /reports/donor/payments/{id}`:
```json
{"type": "about:blank", "title": "Forbidden", "status": 403, "code": "DONOR_AGGREGATE_ONLY",
 "detail": "Donors see aggregated totals only, never individual beneficiaries.",
 "links": {"report": "/api/v1/reports/donor/summary", "payment_counts": "/api/v1/reports/donor/payment-counts"}}
```

### Donor route allowlist (enforced by `tests/contract/test_donor_route_sweep.py`)

`POST /auth/token`, `GET /auth/me`, `GET /districts`, `GET /districts/{id}`, `GET /districts/{id}/tehsils`, `GET /health`, `GET /reports/donor/summary`, `GET /reports/donor/payment-counts`, `GET /reports/donor/stories`, `GET /reports/donor/report.pdf`, `GET /audit/verify`, plus the three denial routes (which return 403). Any other route reachable with a donor token fails the build.

## PDF layout (ReportLab, A4)

1. Header: title "Aab o Hisaab AI — Donor report", as-of (UTC and Asia/Karachi), scope, mode (live/replay + scenario key). Red banner "SIMULATED DATA — prototype, no real money moved" on every page when `simulated`.
2. Headline table: allocated, released, awaiting approval, disbursed, received, awaiting confirmation, reported not received, in flight, unused, unmet need; rates.
3. By district table; by type table; by tier table. Suppressed cells print "< 5".
4. Payment counts by status.
5. Stories (title + body EN, with the label).
6. Audit chain box: VALID/BROKEN, entries, head seq, head hash (first 16 + last 8 hex), first broken entry if any, verified at.
7. Footer on every page: page x/y, `Report fingerprint (SHA-256): <hex>`, "Definitions: see /docs#donor-metrics".

## Scheduled jobs

None.

## Project Structure

### Documentation (this feature)

```text
specs/019-fr19-donor-report/
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
├── schemas/reports.py                   # DonorSummaryOut, Totals, DistrictRow, TypeRow, TierRow, PaymentCountsOut,
│                                        # StoryOut, StoriesOut, ScopeOut (all DonorScope)
├── api/v1/reports.py                    # summary, payment-counts, stories, report.pdf, 3 denial routes
└── services/reporting/
    ├── __init__.py
    ├── donor_scope.py                   # DonorScopeSpec, resolve_donor_scope()
    ├── donor_metrics.py                 # DonorReportService.summary(), payment_counts(), ledger cross-check
    ├── suppression.py                   # suppress_cells(rows, k)
    ├── stories.py                       # StoryProvider, CuratedStoryProvider, OutcomeStoryProvider, load_curated_stories()
    └── donor_pdf.py                     # render_donor_pdf(), report_fingerprint()
data/seeds/donor_stories.yaml
tests/
├── unit/reporting/{test_metric_definitions,test_suppression,test_stories}.py
├── integration/reporting/{conftest,test_donor_summary,test_donor_scope,test_donor_pdf}.py
└── contract/{test_donor_no_pii,test_donor_route_sweep,test_donor_schemas}.py
```

**Structure Decision**: single project per the constitution; reporting code under `app/services/reporting/`, router `app/api/v1/reports.py`.

## Complexity Tracking

None.
