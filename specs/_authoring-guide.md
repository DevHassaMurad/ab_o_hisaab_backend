# Spec Authoring Guide (backend)

Read this before writing or editing any `specs/NNN-*` folder. Also read:
- `.specify/memory/constitution.md`: principles, stack, decisions D1–D6, repo layout
- `specs/000-foundation/data-model.md`: canonical entities. **Do not redefine them.** Reference them, and add fields there if you need new ones.
- `specs/000-foundation/plan.md`: shared service interfaces, API conventions, seed users
- `.specify/templates/{spec,plan,tasks}-template.md`: spec-kit formats
- `../Aab o Hisaab AI Project Document.pdf`: the product source (FR1–FR22, NFR1–NFR13, UC01–UC11)

---

## Folder and file contract

Each FR gets `specs/NNN-frXX-short-name/` with exactly these 6 files:

| File | Must contain |
|---|---|
| **agent.md** | Title `# Agent Brief: FRXX <name>`. Then these sections: **Owner role** (one of the 4 team roles below) · **Mission** (2–3 sentences) · **Scope** (In / Out, with roadmap boundaries) · **Read first** (exact files) · **Depends on / Blocks** (FR ids with reasons) · **Interfaces** (consumes / provides, matching foundation `interfaces.py` names) · **Files you own** (exact paths under `app/`, `tests/`, `data/`, `ml/`) · **Guardrails** (which constitution principles apply and how) · **Definition of Done** (checklist) · **Hand-off notes** (what the next FR needs from you). |
| **spec.md** | Follow `spec-template.md` exactly. Header: `**Feature Branch**: \`NNN-frXX-short-name\``, `**Created**: 2026-11-10`, `**Status**: Draft`, `**Priority**: MVP` or `Roadmap`, `**Input**` = the FR text from the document, verbatim. Then: user stories (P1…) with Given/When/Then, edge cases, functional requirements as **`FRXX-001`, `FRXX-002`…** (prefix with the FR id to avoid collisions across folders), key entities (names from data-model.md), measurable success criteria (`SC-001`…) tied to the relevant NFRs, and assumptions. Keep it technology-agnostic: no FastAPI or SQL here. **Traceability** section at the end: the UCs, NFRs and decisions (D1–D6) this FR implements. |
| **clarify.md** | spec-kit clarify style: `## Clarifications` → `### Session 2026-11-10` → bullets `- Q: … → A: …` for every ambiguity you resolved (state the chosen answer and why). Then `## Resolved by constitution decisions` (list the D-numbers that apply). Then `## Open items` table: `# · Question · Recommended default (used in this spec) · Impact if changed · Who decides`. Every open item must already have a default applied in spec.md and plan.md, so nothing is blocked. |
| **plan.md** | Follow `plan-template.md`. Technical Context filled with the constitution stack. **Constitution Check** table (Principle · How satisfied · ✅/⚠️). **Research decisions** (Decision · Rationale · Alternatives rejected). **Data model slice** (which entities and fields from data-model.md you touch) and **Proposed data-model additions** (if any). **API contracts** (method, path, role, request and response JSON examples, error codes). Scheduled jobs, if any. **Project Structure** with exact files. Complexity Tracking if needed. |
| **tasks.md** | Follow `tasks-template.md` format exactly: `- [ ] T001 [P] [US1] Description with exact file path`. Phases: Setup → Foundational (for this FR) → one phase per user story (tests first, because Principle XI makes tests mandatory for money paths; include them wherever the guide or constitution says so) → Polish. End with **Dependencies & Execution Order** and **Parallel opportunities**. Local numbering T001… per FR. |
| **implement.md** | Build guide for whoever runs `/speckit-implement`: **Execution order** (task ids grouped into steps) · **Key algorithms** with concrete Python sketches (real function names matching plan.md) · **Configuration** (env vars, settings keys, defaults) · **Commands** (alembic, pytest selectors, scripts) · **Verification checklist** mapping each acceptance scenario to a test or a manual step · **Demo notes** (how this FR shows up in the 3-minute replay demo, if it does) · **Pitfalls**. |

Style:
- Plain English, short sentences, tables where they help. No filler.
- Use real numbers from the document: thresholds 40/60/80, increments 10/30/60, split 70/30, 2 h escalation, 6 h/12 h fallbacks, 24 h stuck, 5 min alerts, 15 min forecast.
- Use UTC and PKR. Money is Decimal.
- Roadmap FRs (13, 16, 21, 22) get **full** spec, clarify and plan files. Their tasks and implement files split into "MVP stub (behind a flag)" and "Roadmap build".

---

## Team roles (document Q4)

| Role | Owns |
|---|---|
| **Data & ML** | FR1, FR3, FR4, FR5, FR13 |
| **Satellite & GIS** | FR2, FR21 (plus SRTM elevation and the boundaries in the foundation) |
| **Backend & alerts** | FR6–FR12, FR14–FR20, FR22 |
| **Frontend & pitch** | Consumes the APIs. Frontend specs come later, in the frontend repo |

---

## FR → folder map, sources and key facts

| FR | Folder | MVP? | Use cases |
|---|---|---|---|
| FR1 | `001-fr01-nasa-power-ingestion` | MVP | UC03 (steps 1, 2, 4–6, alt 2a) |
| FR2 | `002-fr02-earth-engine-ingestion` | MVP | UC03 (step 3), UC02 (data side) |
| FR3 | `003-fr03-flood-risk-score` | MVP | UC04 |
| FR4 | `004-fr04-flood-modes` | MVP | UC04 step 2 |
| FR5 | `005-fr05-event-replay` | MVP | UC05 step 4, NFR3 |
| FR6 | `006-fr06-fund-tier-config` | MVP | UC01 |
| FR7 | `007-fr07-auto-tier-release` | MVP | UC04 step 6, UC08 |
| FR8 | `008-fr08-dc-action-approval` | MVP | UC07 |
| FR9 | `009-fr09-beneficiary-split` | MVP | UC01 step 4, UC06 step 4 |
| FR10 | `010-fr10-beneficiary-register` | MVP | UC06 precondition |
| FR11 | `011-fr11-beneficiary-ranking` | MVP | UC06 |
| FR12 | `012-fr12-amount-rules` | MVP | UC06 steps 4–6 |
| FR13 | `013-fr13-llm-explanations` | Roadmap | UC04 step 5 (D2) |
| FR14 | `014-fr14-whatsapp-alerts` | MVP | UC10, UC07 trigger |
| FR15 | `015-fr15-receipt-confirmation` | MVP | UC10 steps 2–3 |
| FR16 | `016-fr16-sms-field-fallback` | Roadmap | UC10 alt 2a/2b |
| FR17 | `017-fr17-payment-tracking` | MVP | UC08, UC09 |
| FR18 | `018-fr18-payment-failure-retry` | MVP | UC08 alt 3a, UC09 steps 3–4 |
| FR19 | `019-fr19-donor-report` | MVP | UC11, UC09 alt 3a |
| FR20 | `020-fr20-audit-log` | MVP | All UCs; NFR7 |
| FR21 | `021-fr21-satellite-verification` | Roadmap (D1: precomputed maps are MVP) | UC02 |
| FR22 | `022-fr22-outcome-reports` | Roadmap | Feeds UC11 step 4 |

### Key facts and decisions per FR (use these, don't contradict them)

- **FR1**: NASA POWER daily point API at each district centroid: `PRECTOTCORR` (rain mm/day), `T2M`, `GWETROOT`, `GWETTOP`. Treat fill value `-999` as missing. Data has a latency of a few days, so record `last_valid_date`. FR1 also owns the World Bank / EasyData context-indicator refresh (UC03-4) that feeds `District.vulnerability_weight`, as a lower-priority story. Validation covers missing values and units (UC03-5). The stale path is UC03-2a (NFR9). The daily job runs ingest and then hands off to FR3 (UC03-6).
- **FR2**: Earth Engine with a service account. IMERG `NASA/GPM_L3/IMERG_V07` (half-hourly) is reduced per tehsil with `reduceRegions` to a daily sum and a max 3-hour intensity. Sentinel-1 `COPERNICUS/S1_GRD` uses VV and change detection. The JRC `JRC/GSW1_4/GlobalSurfaceWater` occurrence above 80% is the permanent-water mask. SRTM `USGS/SRTMGL1_003` gives the mean elevation per tehsil (one-off). Boundaries are OCHA COD-AB (160 districts / 577 tehsils nationally, with the pilot on Sindh and KP). **Verify the dataset IDs in the EE catalog** (versions change). IMERG Early or Late run latency is roughly 4–12 h. Cache results to `data/replay/` for replay events.
- **FR3**: Observations alone cannot forecast 7 days ahead. **Gap you must resolve in clarify.md**: recommend forecast rainfall from the Open-Meteo forecast API (GFS/ECMWF) for live runs. For replays, use Open-Meteo's historical-forecast archive where its coverage includes the event, otherwise use observed rainfall as a clearly-labelled "perfect forecast proxy" (Principle VIII). Features: 3- and 7-day rain, soil wetness, elevation, vulnerability (UC04-1). The model is LightGBM or a logistic baseline per mode, giving a probability for days 1–7. `score = round(100 × p_calibrated)`. `RiskIndex` = max over lead days. The district score is derived from its tehsils (propose a rule in clarify; recommended: the max over tehsils weighted by population share ≥ 10%). The rule-based fallback is UC04-3a. FR3 owns the **template** `ExplainService.explain_risk` (D2), in English and Urdu. Call `TriggerEngine.evaluate` on every run (constitution D4); it acts when the tier rises (UC04-6) and also counts days for the 14-day close rule. Performance: < 15 min for all districts (NFR1).
- **FR4**: `SLOW_ONSET` (Sindh) uses cumulative rain over 7, 14 and 30 days plus soil saturation, with a 7-day lead for Watch. `FLASH` (KP) uses max 3-hour intensity, 24–48 h forecast rain and slope or elevation, with a 24–48 h lead for Action (document Q10). The mode is a district config value, not code. Each mode has its own model artifact and thresholds calibration. Glacial lake outbursts are roadmap.
- **FR5**: Scenarios are `sindh-2022` (hero; Dadu and nearby Sindh districts, about mid-Aug to early Sep 2022), `buner-2025` (flash flood around 15 Aug 2025) and `control-2021` (normal Sindh monsoon, expected NO trigger). Agents must propose exact windows and districts in clarify.md. Replay runs the **real pipeline** day by day using `ReplayClock` and cached inputs in `data/replay/<key>/`. Every row it writes carries `scenario_id`, so replays are isolated from live data. DC approval is simulated as `channel=REPLAY_SIM`, with the option of a real WhatsApp approval during the live demo. Endpoints: start, step, seek to a date, reset, and get state. These backtests are the acceptance tests for NFR3 and live in `tests/backtest/`.
- **FR6**: The PDMA officer edits a district's fund amount, tier thresholds (40/60/80) and release increments (10/30/60), the split and the shopkeeper cap. Validation: increments sum to 100 and thresholds strictly increase within 1–100 (UC01-5). Failed validation saves nothing (UC01-5a). Settings can be copied from another district (UC01-1a). Changes apply from the next daily run and are audited (UC01-6). `requires_approval` on Action is locked.
- **FR7**: `TriggerEngine.evaluate` handles the `TriggerEvent` lifecycle and incremental tiers (D4). It fires every skipped lower tier. Watch and Warning go straight to `FundRelease` → allocation (FR11/12) → payments (FR17) → alerts (FR14). Action creates the release as `PENDING_APPROVAL` and calls FR8. The DC is alerted on any tier change. The engine is idempotent when re-run on the same day. Alerts are queued within 5 min (NFR2).
- **FR8**: `Approval` uses a dashboard OTP or a WhatsApp `APPROVE <code>` / `REJECT <reason>` reply (D3). The detail view shows the score, explanation, 7-day forecast, satellite map (if any) and total amount (UC07-2). A rejection logs the reason, the money stays in the fund, and the PDMA officer is notified (UC07-3a). With no response in 2 h the request escalates to the PDMA officer (D6). The approval starts allocation and payment.
- **FR9**: The household/shopkeeper split defaults to 70/30 and is configurable (0–100). Pools are computed per `FundRelease`. Specify the rounding rule and what happens to an under-used pool (recommended: leftover moves to the other pool within the same release, configurable).
- **FR10**: The register holds households and shopkeepers with location, contact and type-specific fields. PII is encrypted. Phones are validated as Pakistani E.164 (`+923XXXXXXXXX`). Provide CSV import and a **synthetic register generator** (marked simulated), plus derivation of exposure. RBAC: the officer writes, the DC reads their own district, donors get no access.
- **FR11**: Transparent, deterministic, configurable weights. Households: poverty, family size, exposure, minus existing support. Shopkeepers: stock at risk, exposure, existing support, urgency (UC06-2/3). There are deterministic tie-breakers and a reason string per beneficiary in English and Urdu. FR11 owns the **fairness check** (NFR5, UC06-7/7a) and the officer override with a reason (UC06-6).
- **FR12**: Households get a flat amount per tier (`household_amount_by_tier`, e.g. 3000/7000/15000 PKR, configurable, 0 allowed). Shopkeepers get `min(stock_value × stock_pct, cap)` with a default cap of 50 000. Funding goes in rank order until the pool runs out, and the rest is `UNMET_NEED` (UC06-4a). Round to the nearest PKR 100. Handle a beneficiary appearing in several tiers of the same event (recommended: each tier tops up to that tier's amount, not cumulative duplicates; decide in clarify).
- **FR13**: Roadmap. An LLM rewrites the template explanations. Guardrails: every number in the LLM output must appear in the inputs, otherwise use the template. It sits behind flag `FR13_LLM`. Use the Anthropic API (e.g. `claude-haiku-5-5` for cost, `claude-sonnet-5-5` for Urdu quality), with timeouts and caching.
- **FR14**: Twilio WhatsApp, using the sandbox for the demo (recipients must join the sandbox first). In production, business-initiated messages need **pre-approved templates**, so note that. Messages are Urdu first, then English. Content: tier, safety advice, amount and how to collect it (UC10-1). Recipients: the DC (tier change, approval request, escalation) and beneficiaries (tier alert, payment notice). Delivery-status callbacks are handled. Queue → send within 5 min (NFR2). An invalid number flags the beneficiary (UC10-1a).
- **FR15**: FR15 owns the inbound webhook `/api/v1/webhooks/whatsapp`. It validates the Twilio signature. It parses `1` = received and `2` = not received, including Urdu/Arabic-Indic digits `۱`/`١`. It matches the sender by `phone_hash` to the latest open payment notice and records a `Confirmation` plus an audit entry. It **routes DC messages** (`APPROVE …`/`REJECT …`) to the FR8 handler. Duplicates are idempotent via `provider_message_id`.
- **FR16**: Roadmap behind flag `FR16_FALLBACK`. With no reply in 6 h, send an SMS. With still no reply at 12 h, create a `FieldAgentTask`. Timers live in `app/jobs/timers.py`.
- **FR17**: Payment state machine (data-model.md §6). Reserve when the allocation is saved, send via the `PaymentRail` → Processing, then Paid or Failed. Fund balances are updated in the same transaction. `PaymentEvent` holds the timeline. Flag a payment as stuck after more than 24 h in Processing (UC09-1a). Provide list, filter and count-by-status endpoints and a CSV export (UC09-1/2/5). Paid triggers the FR14 payment notice (UC08-5). `SimulatedRail` produces deterministic failures (e.g. wallet ids ending `000` → `INVALID_WALLET`, amounts above the wallet limit → `LIMIT_REACHED`, plus a configurable random failure rate).
- **FR18**: Failure reasons, and retry (at most 3, with a new idempotency key) or cancel (returns the money to the fund) by the **DC of that district**. Every action needs a note and is audited (UC09-4).
- **FR19**: The donor report shows allocated, disbursed, received (= Paid with a CONFIRMED confirmation) and unused, broken down by district and beneficiary type (UC11-2/3). It includes anonymised outcome stories (in the MVP, curated stories labelled simulated, until FR22 exists), a one-click chain verify (calls FR20), and a PDF download. Requests for individual data return 403 (UC11-3a). Donors also get **aggregate payment counts** (UC09-3a). A contract test must prove no PII fields are present (NFR8). Define each metric precisely in spec.md.
- **FR20**: The hash chain from the constitution (Principle III). Use `pg_advisory_xact_lock` to serialise appends (seq + prev_hash). A DB trigger blocks UPDATE and DELETE. Each `scenario_id` has its own chain. Endpoints: `GET /audit` (officer/admin, filterable), `GET /audit/verify` (any role including donor; returns valid, count, head hash and the first broken seq) and an export. Payloads contain no PII. FR20 replaces the foundation stub `AuditService`.
- **FR21**: Roadmap, except that the D1 MVP covers a **precompute script** for the replay events' before/after Sentinel-1 maps, plus `GET /satellite/flood-maps?tehsil=&date=`. The roadmap part is a live job behind `FR21_LIVE_VERIFY` that runs when a new S1 image arrives within 7 days of a trigger and marks `TriggerEvent.confirmation_status` (UC02 and its alternate flows). Unconfirmed results are flagged for PDMA review.
- **FR22**: Roadmap behind `FR22_OUTCOME` (canonical flag name, D7). A short WhatsApp survey (Urdu first) N days after payment. Answers are stored as `OutcomeReport`, then anonymised and aggregated into stories for FR19.

---

## Shared rules every FR must respect

1. Every state change calls `AuditService.record(...)` in the same transaction (Principle III).
2. Every operational write carries `scenario_id` from `ScenarioContext` (FR5 isolation).
3. Every simulated artefact sets `simulated=true` (Principle VIII).
4. Never hard-code districts, thresholds or amounts (Principle IX).
5. Donor-facing schemas inherit `DonorScope` (Principle VI).
6. Endpoints follow the API conventions in `000-foundation/plan.md`.
7. Do not invent other entities, and do not edit `000-foundation/data-model.md` directly. If you truly need a field, list it under **"Proposed data-model additions"** in your plan.md, and the foundation owner merges it into data-model.md.
