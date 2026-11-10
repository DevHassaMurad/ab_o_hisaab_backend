# Agent Brief: FR19 Donor report

## Owner role
Backend & alerts (the Frontend & pitch role consumes the API and the PDF).

## Mission
Show donors, in aggregate only, how their funds were allocated, released, disbursed, received and left unused, broken down by district and beneficiary type, with anonymised outcome stories, a one-click audit-chain verify and a downloadable PDF. Prove with a contract test that no beneficiary-level data can ever reach a donor (NFR8).

## Scope
**In (MVP)**
- Precise metric definitions and their SQL (spec.md and plan.md are normative)
- `GET /api/v1/reports/donor/summary` (headline metrics, by district, by type, district × type, by tier, confirmation counts)
- `GET /api/v1/reports/donor/payment-counts` (aggregate counts by status, UC09-3a)
- `GET /api/v1/reports/donor/stories` (curated stories labelled simulated in the MVP; FR22-backed stories behind its flag)
- `GET /api/v1/reports/donor/report.pdf` (ReportLab; includes the verify result and a report hash; audited)
- Explicit denial routes `GET /api/v1/reports/donor/beneficiaries`, `/reports/donor/payments`, `/reports/donor/payments/{id}` → 403 `DONOR_AGGREGATE_ONLY` (UC11-3a)
- Small-cell suppression for breakdown cells
- Contract tests: schema deny-list, route sweep for the donor role, canary-PII scan of every donor response including the PDF

**Out**
- The verify algorithm itself: `020-fr20-audit-log` (`GET /audit/verify`, `verify_chain()`)
- Collecting outcome reports: `022-fr22-outcome-reports` (roadmap); FR19 only reads its aggregates when the flag is on
- Confirmations: `015-fr15-receipt-confirmation` writes them; FR19 reads them
- Donor sign-up and contribution management: seeded (`DonorContribution` in foundation seeds)
- Urdu text in the PDF: roadmap (the JSON API carries Urdu story text)

## Read first
- `.specify/memory/constitution.md` (Principles III, VI, VIII, XI)
- `specs/_authoring-guide.md` (FR19 facts)
- `specs/000-foundation/data-model.md` §4 `DisasterFund`, `FundRelease`; §5 `Allocation`, `Beneficiary` (type only), `OutcomeReport`; §6 `Payment`; §7 `Confirmation`; §8 `DonorContribution`, `User`
- `specs/000-foundation/implement.md` (`DonorScope`, `require_role.__roles__`, `scoped()`)
- `specs/017-fr17-payment-tracking/plan.md` (ledger meaning, `count_by_status()`)
- `specs/020-fr20-audit-log/plan.md` (`verify_chain`, `canonical_json`)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
- **Depends on**: `000-foundation` (`DonorScope`, roles, `DonorContribution` seeds), `020-fr20-audit-log` (verify + audit), `017-fr17-payment-tracking` (payments and fund ledger), `015-fr15-receipt-confirmation` (for "received"; without it, received is 0 and the report says so).
- **Reads (soft)**: `007-fr07-auto-tier-release` (`FundRelease` statuses), `012-fr12-amount-rules` (`UNMET_NEED`), `018-fr18-payment-failure-retry` (cancelled money), `022-fr22-outcome-reports` (flag `FR22_OUTCOME`).
- **Blocks**: nothing in the backend; the donor screen and the pitch's last demo beat.

## Interfaces
- **Provides**: `DonorReportService.summary(db, scope) -> DonorSummaryOut`, `payment_counts(db, scope)`, `StoryProvider` Protocol with `CuratedStoryProvider` (MVP) and `OutcomeStoryProvider` (FR22 flag), `render_donor_pdf(summary, stories, verify) -> bytes`, `resolve_donor_scope(db, user, district_id) -> DonorScopeSpec`.
- **Consumes**: `verify_chain()` and `current_chain_key()` (FR20), `canonical_json()` (FR20), `AuditService.record` (FR20), `count_by_status()` (FR17), `current_scenario()`, `require_role`, `DonorScope` base schema.

## Files you own
- `app/services/reporting/__init__.py`, `donor_scope.py`, `donor_metrics.py`, `suppression.py`, `stories.py`, `donor_pdf.py`
- `app/schemas/reports.py` (all models inherit `DonorScope`)
- `app/api/v1/reports.py`
- `data/seeds/donor_stories.yaml`
- `tests/unit/reporting/test_metric_definitions.py`, `test_suppression.py`, `test_stories.py`
- `tests/integration/reporting/conftest.py`, `test_donor_summary.py`, `test_donor_scope.py`, `test_donor_pdf.py`
- `tests/contract/test_donor_no_pii.py`, `test_donor_route_sweep.py`, `test_donor_schemas.py`
- One line in `pyproject.toml` adding `reportlab` (coordinate with the foundation owner)

## Guardrails
- **VI / NFR8 (non-negotiable for this FR)**: every response model inherits `DonorScope`; no beneficiary, payment, allocation, confirmation or alert ids, no names, phones, CNICs, wallets, coordinates, notes or tehsil-level rows; small cells suppressed. The PDF is generated only from the same `DonorSummaryOut` object, never from raw rows.
- **III**: each PDF generation writes `DONOR_REPORT_GENERATED` with the report's SHA-256 and the chain head, so a printed report can be tied to the log.
- **VIII**: `simulated=true` and a visible "SIMULATED DATA" banner whenever any underlying payment, release or story is simulated (always, in the MVP). Curated stories carry `source=CURATED` and the label "Illustrative story (simulated)".
- **II**: donors are read-only; FR19 has no write endpoints for them.
- **XI**: metric tests, scope tests and the no-PII contract tests are written first.

## Definition of Done
- [ ] Each metric in spec.md has a unit test on a hand-built fixture with known answers, and the identities hold (`allocated = disbursed + in_flight + unused`, `received ≤ disbursed ≤ released ≤ allocated`)
- [ ] Donor sees only funds/districts linked through `DonorContribution`; other districts → 403 `FUND_NOT_LINKED`
- [ ] Route sweep: the donor role can reach only the allowlisted routes; all others return 403
- [ ] Canary test: none of the seeded canary names/phones/CNICs/wallets/UUIDs appear in any donor JSON response or the PDF bytes
- [ ] PDF downloads in < 5 s, shows the SIMULATED banner, metrics, breakdowns, stories and the verify result; one `DONOR_REPORT_GENERATED` audit entry per download
- [ ] Replay header gives the replay's numbers; live view excludes replay data
- [ ] Summary p95 < 500 ms at 50 000 payments

## Hand-off notes
- Frontend: the "Verify chain" button calls `GET /api/v1/audit/verify` (FR20) with the same `X-Scenario-Id`; FR19 returns `audit_verify_url` for convenience. Show `suppressed` cells as "fewer than 5".
- `015-fr15-receipt-confirmation`: FR19 counts a payment as received when its **latest** non-`UNPARSEABLE` confirmation is `CONFIRMED`. Please keep `Confirmation.payment_id` set for payment-notice replies.
- `022-fr22-outcome-reports`: implement `OutcomeStoryProvider.get_stories(scope)` returning aggregated stories with `source=OUTCOME_AGGREGATE`, `group_size ≥ DONOR_MIN_CELL_SIZE`; FR19 switches to it when `FR22_OUTCOME` is on.
