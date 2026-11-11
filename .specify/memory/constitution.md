<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0 (2026-11-10, reconciliation after all 22 FR specs were drafted)
- Modified: Principle III (the hash covers the whole entry envelope; one chain per live/replay run; replay reset never deletes audit rows); Principle VIII (precomputed maps are labelled "precomputed", not simulated); D3 (allocation order, rejection outcome); D4 (the trigger engine is evaluated every daily run); Quality Standards (NFR2 = handed to the provider ≤ 5 min; simulated-beneficiary messaging allowlist)
- Added: D7 (shared names for the foundation helpers)
- 1.0.0: initial ratification (Principles I–XI, stack, D1–D6, standards, workflow, governance)
- Templates checked: plan-template.md ✅ (Constitution Check gate references this file), spec-template.md ✅, tasks-template.md ✅ (tests are mandatory for money paths per Principle XI)
- Deferred TODOs: none
-->

# Aab o Hisaab AI Backend Constitution

**Mission**: Predict floods before they strike and automatically release anticipatory funds to the most vulnerable households and small businesses, so every rupee arrives on time, reaches the right people, and can be traced from donor to doorstep.

**Source of truth**: `../Aab o Hisaab AI Project Document.pdf` (22 pages, 10 Nov 2026). It defines FR1–FR22, NFR1–NFR13 and UC01–UC11. If a spec in `specs/` conflicts with that document, this constitution and its Resolved Product Decisions (D1–D6) win. The document comes next, then the spec.

---

## Core Principles

### I. Anticipate, Don't React
The system exists to move money **before** water arrives.
- Each district and tehsil gets a 0–100 flood risk score every day, up to 7 days ahead (FR3).
- Releases are driven by tier thresholds, not by post-disaster damage assessment.
- Any feature that adds latency between "the score crosses a threshold" and "money moves" must justify itself in the plan's Complexity Tracking table.

### II. Humans Hold Authority Over Large Money (NON-NEGOTIABLE)
The AI advises; officials decide (NFR12).
- Watch and Warning tiers release automatically (FR7).
- The Action tier is never released without a recorded Deputy Commissioner `Approval` (FR8).
- No code path, admin endpoint, script or replay shortcut may bypass this. Replay mode simulates the DC's approval with an explicit `simulated=true` approval record; it never skips the record.
- Donors fund and view only. They never change settings, approve or allocate.

### III. Every Rupee Is Traceable (NON-NEGOTIABLE)
Each of the following writes exactly one entry to the hash-chained `AuditLog` (FR20, NFR7), **in the same database transaction** as the change:
- every change to fund settings
- every trigger firing
- every approval or rejection
- every allocation and override
- every payment status change
- every alert and confirmation

Each entry stores `prev_hash` and `hash = SHA-256(prev_hash ‖ canonical_json(envelope) ‖ timestamp)`. The envelope is `{chain_key, seq, event_type, entity_type, entity_id, actor, scenario_id, payload}`, so editing any field, not only the payload, is detectable (NFR7). The normative spec is `specs/020-fr20-audit-log/plan.md`.
- There is one chain per `chain_key`: `live`, or `replay:{scenario_id}:{run_no}`. `seq` is unique within its chain.
- A replay reset starts a new run (`run_no + 1`) and therefore a new chain. It never deletes audit rows.

Audit rows are append-only:
- No `UPDATE` or `DELETE` on the table. This is enforced by a DB trigger, not only by convention.
- A verify endpoint recomputes the chain end to end.

### IV. Explainable by Construction
Every risk score and every payment amount must trace back to its inputs and rules (NFR4).
- A `RiskIndex` stores the feature values, model version and mode that produced it.
- An `Allocation` stores the rule inputs, the computed priority score, the amount formula and any override reason.
- Plain-language explanations (English + Urdu) are generated from templates over those stored inputs (D2). An LLM may rephrase them, but the template text is always stored and always the fallback.

### V. Fair and Transparent Allocation
- Ranking rules are published, deterministic and configurable (FR11).
- The same inputs always give the same ranking.
- Manual overrides require a written reason and are audited.
- A fairness check runs before every allocation is saved (NFR5). It flags when districts or tehsils with sparse data get a share that falls well below their vulnerability-weighted share. The officer must see the warning before saving.

### VI. Privacy by Role
Access is role-based with these roles: `PDMA_OFFICER`, `DC`, `DONOR`, `ADMIN` (NFR6).
- Beneficiary PII (name, phone, CNIC, wallet id) is encrypted at column level.
- Donor-facing endpoints return **aggregates only** (NFR8). Their response schemas physically contain no beneficiary-level fields, and a contract test proves it.
- A DC sees only their own district.

### VII. Degrade Gracefully, Never Silently
- If a data source fails, use the last valid data, mark the result `is_stale=true` with the date of that last valid data, and alert the admin (NFR9).
- If the model fails, use the rule-based rainfall score and mark the result `is_fallback=true` (UC04-3a).
- Stale and fallback flags travel all the way to the API response and the audit log.

### VIII. Honest Prototype
- Anything simulated is labelled `simulated: true` in the data and in API responses. This covers payment rails (JazzCash/Easypaisa), beneficiary registers and DC approvals in replay (NFR13). Precomputed satellite maps are real data, not simulated, so they carry `precomputed: true` instead (D1). The UI must show that label.
- A demo must never be able to pass simulated money off as real.

### IX. Configuration Over Code
- Adding a district, tehsil or province, changing tier thresholds, release percentages, the household/shopkeeper split, per-tier household amounts or shopkeeper caps is a **data/config change**, never a code change (NFR11, FR6, FR9).
- No district names, thresholds or amounts are hard-coded outside seed files and defaults.

### X. Demo-First Simplicity
The MVP is a 32-hour build that must survive a 3-minute live replay demo (Sindh 2022, Buner 2025, the 2021 control year).
- MVP FRs (18) are built fully. Roadmap FRs (FR13, FR16, FR21, FR22) are specified fully but implemented only to the minimum stated in their `implement.md`, behind feature flags.
- Prefer one FastAPI service, one Postgres database and in-process scheduling over distributed infrastructure. YAGNI applies.

### XI. Test the Money Paths
Tests are **mandatory** (written first, failing first) for:
- tier evaluation and the incremental release maths
- Action-tier approval gating
- allocation ranking and amount calculation, including caps and the budget-short path
- the payment state machine
- audit hash chaining and verification
- donor-endpoint aggregation (no PII leaks)

Backtests are acceptance tests (NFR3): Sindh 2022 and Buner 2025 must trigger, and the 2021 control year must produce **no** trigger. Other areas need tests where they protect a contract.

---

## Technology Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI + Pydantic v2, versioned under `/api/v1` |
| Database | PostgreSQL 16 + PostGIS (district/tehsil geometries) |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Scheduling | APScheduler (in-process) for the daily pipeline and timers (escalations, SMS fallback, stuck payments) |
| ML | pandas, scikit-learn / LightGBM; model artifacts versioned in `ml/artifacts/` |
| Earth observation | Google Earth Engine Python API (GPM IMERG, Sentinel-1, JRC Global Surface Water, SRTM) |
| Weather | NASA POWER REST API; forecast rainfall source per FR3 `clarify.md` |
| Messaging | Twilio WhatsApp (sandbox for the demo) + Twilio SMS; inbound webhooks |
| Payments | `PaymentRail` adapter interface; MVP ships `SimulatedRail` only (JazzCash/Easypaisa-shaped) |
| Auth | JWT access tokens (OAuth2 password flow), bcrypt hashes, role claims; 6-digit OTP for DC approvals |
| Encryption | Fernet (cryptography) column encryption for PII; key from env |
| Reports | WeasyPrint (or ReportLab) for donor PDF; CSV via stdlib |
| Testing | pytest, pytest-asyncio, httpx TestClient, factory fixtures, a testcontainers Postgres or local Postgres |
| Quality | ruff (lint + format), mypy (strict on `app/services` and `app/models`) |

**Repository layout** (all FR plans must use these paths):

```text
app/
├── main.py                  # FastAPI app factory, router registration
├── core/                    # config.py, db.py, security.py, crypto.py, logging.py, scheduler.py, flags.py
├── models/                  # SQLAlchemy models (one file per aggregate)
├── schemas/                 # Pydantic request/response models
├── api/v1/                  # routers: auth, districts, settings, ingestion, forecasts, replay, beneficiaries,
│                            #          allocations, approvals, releases, payments, alerts, webhooks, reports, audit, satellite
├── services/                # business logic: ingestion/, forecasting/, triggers/, beneficiaries/, allocation/,
│                            #                 payments/, messaging/, audit/, reporting/, satellite/, explain/
├── integrations/            # nasa_power.py, earth_engine.py, forecast_rain.py, twilio_client.py, payment_rail.py, llm.py
└── jobs/                    # daily_pipeline.py, timers.py
ml/                          # training scripts, feature definitions, artifacts/
data/                        # boundaries/ (OCHA COD-AB), seeds/, replay/ (cached historical inputs)
alembic/
tests/                       # unit/, integration/, contract/, backtest/
specs/                       # one folder per FR (see Development Workflow)
```

---

## Resolved Product Decisions

These resolve contradictions in the project document. Specs must follow them.

- **D1 Satellite confirmation (FR21, UC02)**: The MVP shows **precomputed** Sentinel-1 before/after flood maps for the replay events (Sindh 2022, Buner 2025), stored as `SatelliteFloodMap` rows with `simulated=false, precomputed=true`. The live daily job that marks triggers Confirmed, Not confirmed or Pending is roadmap.
- **D2 Explanations (FR13, UC04-5)**: The MVP generates explanations from **templates** in English and Urdu over stored inputs. An LLM rewrite is roadmap, behind a flag, and the template text is always the fallback.
- **D3 DC approval (FR8, UC07)**: There are two channels and one record.
  - **WhatsApp**: the DC replies `APPROVE <code>` or `REJECT <reason>`.
  - **Dashboard**: the DC uses the Approve button plus the 6-digit OTP.
  - Both channels create the same `Approval` row, with its `channel` recorded.
- When Action fires, the allocation list is **drafted** so the DC can see the counts and amounts. It is **saved**, and payments are reserved, only after approval (FR11, FR17). No money is reserved for an unapproved release.
- If the DC rejects, the release is `REJECTED` and its money **stays in the district fund**, unreserved and available. This is how UC07-3a's "funds stay reserved" is read. The PDMA officer is notified.
  - If there is no response in 2 hours, the request escalates to the PDMA officer's dashboard (see D6).
- **D4 Incremental tiers**: Release percentages are **increments** that sum to 100.
  - Watch releases 10% (10% released in total), Warning releases 30% more (40% in total) and Action releases 60% more (100% in total).
  - Each tier fires **at most once per `TriggerEvent`** (one flood episode per district).
  - If a score jumps several tiers, every lower tier fires automatically and Action waits for approval.
  - If the score falls, nothing is clawed back and unreleased money stays in the fund.
  - An event closes after 14 consecutive days below Watch. This number is configurable.
  - `TriggerEngine.evaluate` runs on **every** daily run, not only when the tier rises, so the close rule can count days. Re-running the same date changes nothing.
- **D5 Data model additions**: Add `Payment` (one beneficiary payment, holding the state machine), `Confirmation`, `Approval` and `User`. Add `TriggerEvent` and `Allocation` as supporting entities. `DisasterFund` gains `household_share_pct`, `shopkeeper_cap_pkr`, `household_amount_by_tier`. `FundRelease` is the district-level tranche for one tier. The canonical model is `specs/000-foundation/data-model.md`.
- **D6 Director General**: Roadmap only. In the MVP an unanswered Action approval escalates to the PDMA officer: it is flagged on their dashboard and they get a WhatsApp notice. The officer still cannot approve; only the DC can.

- **D7 Shared helper names**: all specs use the foundation names. These are `current_scenario()` / `use_scenario()` (not `ScenarioContext.get()`), `flags.is_enabled(...)` (not `is_on`), and the flags `FR13_LLM`, `FR16_FALLBACK`, `FR21_LIVE_VERIFY`, `FR22_OUTCOME`.

- **D8 English-only specs**: All specs are written in English only. Beneficiary-facing messages are specified in English; the Urdu translation is produced later by a native speaker into one translation file (`app/services/messaging/templates/ur.yaml`). Code sketches mark translation slots as `"[Urdu text]"`. Urdu and Arabic-Indic digits in replies are still accepted; specs refer to them by name or code point (for example U+06F1).

---

## Quality Standards

| Area | Standard | Source |
|---|---|---|
| Daily forecast | All configured districts finish within **15 min** | NFR1 |
| API reads | Dashboard read endpoints p95 **< 500 ms**, so pages load in < 3 s | NFR1 |
| Alert latency | Alerts are **handed to the provider ≤ 5 min** after a trigger fires (`sent_at − triggered_at`) | NFR2 |
| Demo safety | Synthetic (simulated) beneficiaries are only messaged if their number is on the demo allowlist, because synthetic numbers may belong to real people | VIII |
| Backtests | Sindh 2022 and Buner 2025 trigger, and 2021 does not | NFR3 |
| Audit | 100% of state changes are audited and the chain verifies | NFR7, FR20 |
| Privacy | 0 beneficiary-level fields in any donor response | NFR8 |
| Language | Every beneficiary-facing message has Urdu first, then English, at a low-literacy reading level | NFR10 |
| Money | Integer paisa or `Decimal(14,2)` PKR, never floats | — |
| Time | All timestamps are UTC `timestamptz`; display time is Asia/Karachi | — |
| Idempotency | Payment instructions and webhooks are idempotent (idempotency keys, provider message ids) | — |

---

## Development Workflow

1. **One folder per FR**: `specs/NNN-frXX-short-name/`, where NNN equals the FR number. `000-foundation` holds shared infrastructure. Each folder contains:

   | File | Purpose |
   |---|---|
   | `agent.md` | Brief for the human owner or AI coding agent: role, scope, dependencies, boundaries, definition of done |
   | `spec.md` | What and why: user stories, acceptance scenarios, requirements, success criteria. Technology-agnostic |
   | `clarify.md` | Questions asked, answers adopted, open items with a recommended default |
   | `plan.md` | How: technical context, Constitution Check, data model slice, API contracts, structure |
   | `tasks.md` | Ordered, dependency-aware checklist (`T001 [P] [US1] …`) with exact file paths |
   | `implement.md` | Build guide: execution order, key algorithms or pseudocode, config, test commands, verification and demo steps |

2. **Order**: `000-foundation` → FR20 (audit), because everything writes to it → Data (FR1, FR2) → Forecasting (FR3–FR5) → Triggers (FR6–FR9) → Beneficiaries (FR10–FR12) → Payments (FR17, FR18) → Communication (FR14, FR15) → Reporting (FR19) → Roadmap (FR13, FR16, FR21, FR22).
3. **Branches**: Use `NNN-frXX-short-name`, matching the spec folder. Merge to `main` only when the FR's definition of done in `agent.md` is met and its tests pass.
4. **Review gate**: Every PR states which principles it touches. A PR that touches money, approval, audit or donor endpoints needs a second reviewer.
5. **Spec drift**: If implementation must differ from `spec.md` or `plan.md`, update the spec in the same PR.

---

## Governance

- This constitution supersedes other practices in this repo. Every `plan.md` has a Constitution Check gate that must pass, or record a justified violation in Complexity Tracking.
- Principles II and III are non-negotiable and cannot be waived by Complexity Tracking.
- Amendments require: a PR that edits this file, a Sync Impact Report at the top, a version bump, and updates to any affected specs.
- Versioning follows semver:
  - **MAJOR**: a principle is removed or redefined.
  - **MINOR**: a principle, section or decision is added.
  - **PATCH**: wording changes only.

**Version**: 1.1.0 | **Ratified**: 2026-11-10 | **Last Amended**: 2026-11-10
