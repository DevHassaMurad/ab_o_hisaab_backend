# Feature Specification: FR5 Event Replay

**Feature Branch**: `005-fr05-event-replay`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "Replay past events (Sindh 2022, Buner 2025, a normal year) day by day" (FR5, Forecasting, MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a past event day by day through the real pipeline (Priority: P1)

A PDMA officer (or the presenter) starts a replay of a past event. Each step advances a simulated date by one day and runs the same daily pipeline as live: ingest cached data, forecast, fire tiers, allocate, pay (simulated), alert. Everything the replay writes is kept apart from live data.

**Why this priority**: The live demo is a replay (Q5, Q6), and the replay is how the team proves the system would have acted before the 2022 floods.

**Independent Test**: Start the `mini` fixture scenario (1 district, 5 days), step 5 times, and check that scores, events and releases exist for that scenario only, and that live tables are unchanged.

**Acceptance Scenarios**:

1. **Given** a scenario that has not started, **When** the officer starts it, **Then** its simulated date is the day before its first day, its funds and tiers are created for the scenario, and its status is Running.
2. **Given** a running scenario, **When** the officer steps one day, **Then** the full daily pipeline runs for the next simulated date, using only data that would have been available on that date, and every row written carries the scenario id.
3. **Given** a replay has run, **When** live data is read without a scenario header, **Then** none of the replay's rows appear.
4. **Given** the last day of the window has been run, **When** the officer steps again, **Then** the scenario is marked Completed and nothing more runs.
5. **Given** a step is in progress, **When** another step is requested for the same scenario, **Then** it is refused as busy.

---

### User Story 2 - Reproducible cached inputs (Priority: P1)

Each scenario's inputs (NASA POWER, IMERG per tehsil, forecast rainfall with provenance, precomputed Sentinel-1 maps) are built once from the real sources, saved in the repository with a manifest of file hashes, and verified before every replay.

**Why this priority**: The demo must not depend on venue Wi-Fi or on APIs changing their archives, and backtests must give the same answer every time.

**Independent Test**: Build the cache for `buner-2025`, then change one byte of a cached file; starting the scenario is refused with a cache error.

**Acceptance Scenarios**:

1. **Given** network access and credentials, **When** the cache build runs for a scenario, **Then** every source file and a manifest with hashes, row counts and forecast provenance counts are written.
2. **Given** a cached file does not match the manifest, **When** the scenario is started, **Then** it is refused and nothing is written.
3. **Given** a scenario whose forecast rainfall is a perfect forecast proxy for some days, **When** its state is read, **Then** the proxy share is shown.

---

### User Story 3 - Backtests as acceptance tests (Priority: P1)

The three scenarios run headless from start to end. Sindh 2022 and Buner 2025 must trigger with enough lead time; the 2021 control must produce no trigger at all (NFR3). A report states what fired, when and why.

**Why this priority**: NFR3 and Principle XI make these the acceptance tests; they are also the pitch's "proof" line.

**Independent Test**: `pytest -m backtest` runs all three and fails if any expectation is missed.

**Acceptance Scenarios**:

1. **Given** the Sindh 2022 scenario, **When** it runs to the end, **Then** Dadu has at least one fund release, and its first release is at least 3 days before the reference onset (22 Aug 2022).
2. **Given** the Buner 2025 scenario, **When** it runs to the end, **Then** Buner has at least one fund release, and its first release is at least 1 day before the reference onset (15 Aug 2025).
3. **Given** the 2021 control scenario, **When** it runs to the end, **Then** no fund release exists and every district's score stays below its Watch threshold on every day.
4. **Given** any backtest, **When** an Action release exists, **Then** it has an approval record.
5. **Given** a backtest finishes, **When** the report is produced, **Then** it shows the first date per tier, lead days against the reference onset, maximum score, forecast provenance, model versions and pass/fail.

---

### User Story 4 - Simulated or real DC approval in replay (Priority: P1)

When a replay reaches the Action tier, the Deputy Commissioner's approval is either simulated (recorded as a replay simulation, labelled simulated) so the replay continues, or requested for real on WhatsApp during the live demo; the replay pauses until the DC answers.

**Why this priority**: Principle II is non-negotiable: Action never moves without an approval record, even in a replay.

**Independent Test**: Run the mini scenario to Action in simulated mode and check the approval row's channel and label; run again in live mode and check the replay pauses at Action until an approval is recorded.

**Acceptance Scenarios**:

1. **Given** simulated approval mode, **When** Action fires, **Then** an approval is recorded with channel "replay simulation", labelled simulated, and the release proceeds in the same step.
2. **Given** live approval mode, **When** Action fires, **Then** the scenario status becomes Awaiting approval and further steps are refused until the DC approves or rejects.
3. **Given** live approval mode, **When** the DC replies `APPROVE <code>` on WhatsApp, **Then** the approval is recorded for that scenario and the next step continues.
4. **Given** the DC rejects, **When** the replay continues, **Then** the Action money stays in the scenario's fund.

---

### User Story 5 - Seek to a date and reset (Priority: P2)

The officer can jump forward to a date (running every day in between) or reset the scenario to start again. Going backwards always means reset and re-run, because past effects cannot be undone.

**Why this priority**: Rehearsals and the demo need fast "jump to 18 Aug" and "start over".

**Independent Test**: Seek the mini scenario to day 4, check all four days ran; reset, check the scenario's operational rows are gone, the audit run number increased and the previous audit chain still verifies.

**Acceptance Scenarios**:

1. **Given** the simulated date is 5 Aug, **When** the officer seeks to 18 Aug, **Then** each day from 6 to 18 Aug runs in order.
2. **Given** the simulated date is 18 Aug, **When** the officer seeks to 10 Aug without asking to reset, **Then** the request is refused.
3. **Given** the officer seeks backwards with reset, **When** it completes, **Then** the scenario was reset and re-run to 10 Aug.
4. **Given** a reset, **When** it completes, **Then** the scenario's scores, events, releases, allocations, payments, alerts and approvals are removed, its audit entries remain, and a new audit chain starts.

---

### User Story 6 - Timeline for the replay slider and autoplay (Priority: P2)

The dashboard shows a slider through the event (UC05-4): for every simulated day so far, each district's score, tier and any release. For the stage, autoplay advances one day every few seconds and stops at the end or when a live approval is needed.

**Why this priority**: This is what the audience sees during the 3-minute demo.

**Independent Test**: After 10 steps, the timeline returns 10 days per district; play advances days on its own and pauses on request.

**Acceptance Scenarios**:

1. **Given** 10 simulated days have run, **When** the timeline is requested, **Then** it returns one entry per day and district with score, tier and releases, plus fund remaining.
2. **Given** autoplay at 3 seconds per day, **When** it runs, **Then** one day is stepped about every 3 seconds until paused, completed or awaiting approval.
3. **Given** a DC user, **When** they read a replay timeline, **Then** they see only their district.

### Edge Cases

- A cached file is missing for one district: start is refused (all-or-nothing), naming the file.
- A day's cached NASA POWER data falls in a configured outage: the pipeline's stale path runs (FR1), so the stale banner can be demonstrated.
- The model artifact changes between runs: the backtest report records the model versions used; the manifest is not affected.
- A step fails half-way (exception in a pipeline step): the scenario is marked Failed with the error; re-stepping the same day is allowed because every step is idempotent.
- Two scenarios run at the same time: allowed; each has its own scenario id, lock and clock.
- Live pipeline runs during a replay: unaffected (different scenario context).
- Reset during autoplay: autoplay stops first.
- Seek beyond the end date: runs to the end and marks Completed.
- DC approves after the replay has been reset: the approval belongs to the old run and is refused (release not found in the current run).

## Requirements *(mandatory)*

### Functional Requirements

- **FR5-001**: The system MUST provide three scenarios: `sindh-2022`, `buner-2025` and `control-2021`, each with a fixed window, district list, expected outcome, reference onset per hero district and minimum lead days, defined in configuration.
- **FR5-002**: A replay step MUST run the same daily pipeline code as live for the next simulated date, with the simulated date supplied by a replay clock.
- **FR5-003**: Every row written during a replay MUST carry the scenario id; live reads MUST NOT see replay rows.
- **FR5-004**: Replay inputs MUST come only from cached files, and each source MUST return only data that would have been available on the simulated date.
- **FR5-005**: Cached inputs MUST be verified against a manifest of hashes before a scenario starts.
- **FR5-006**: Starting a scenario MUST create scenario-specific funds and tiers, capture district flood modes, and load 30 days of spin-up observations before the first day.
- **FR5-007**: In simulated approval mode, every Action release MUST get an approval record with channel `REPLAY_SIM`, labelled simulated, before it is released.
- **FR5-008**: In live approval mode, the replay MUST pause at a pending Action approval until a DC decision is recorded through the normal channels.
- **FR5-009**: The system MUST support start, step (one or more days), seek to a date, reset, state and timeline; seeking backwards MUST require reset.
- **FR5-010**: Reset MUST remove the scenario's operational rows, keep all its audit entries, and start a new audit chain for the next run.
- **FR5-011**: Only one step at a time MUST run per scenario.
- **FR5-012**: The backtest for each scenario MUST pass these rules: `TRIGGER` → the hero district has at least one fund release and the first one is at least the minimum lead days before the reference onset; `NO_TRIGGER` → no fund release in any scenario district and every district score stays below that district's Watch threshold on every day.
- **FR5-013**: The backtest MUST also check that every Action release has an approval record and that live data is unchanged.
- **FR5-014**: The system MUST produce a backtest report with first date per tier, lead days, maximum score, forecast provenance, model versions and the pass/fail result.
- **FR5-015**: Every replay response MUST state that it is simulated and show the scenario's forecast provenance (Principle VIII).
- **FR5-016**: Autoplay MUST step one day per configured interval and stop at the end, on pause, on failure or when awaiting a live approval.
- **FR5-017**: Starting, stepping, seeking, resetting and autoplay MUST be limited to PDMA officers and admins; state and timeline are readable by officers, admins, DCs (own district only) and donors (district-level aggregates only).

### Key Entities

- **ReplayScenario**: key, window, districts, expected outcome, status, simulated date, run number, configuration (onsets, lead rules, approval mode, mode snapshot, manifest hash).
- **Operational entities with scenario id**: ClimateObservation, FloodForecast, RiskIndex, DisasterFund, TriggerEvent, FundRelease, Payment, Alert, SatelliteFloodMap (and their children Approval, Allocation, FairnessCheck, PaymentEvent, Confirmation through their parents).
- **AuditLog**: one chain per scenario run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Sindh 2022 and Buner 2025 backtests trigger and the 2021 control produces zero triggers, on every run of the test suite (NFR3).
- **SC-002**: Sindh 2022's first Dadu release is ≥ 3 days before 22 Aug 2022 (target 7 days, Q10); Buner's first release is ≥ 1 day before 15 Aug 2025 (target 24–48 h).
- **SC-003**: After all three replays, 0 live rows were added, changed or deleted.
- **SC-004**: 100% of Action releases in replays have an approval record.
- **SC-005**: One replay day for up to 5 districts completes in under 6 seconds; a full Sindh 2022 run in under 4 minutes.
- **SC-006**: Two consecutive runs of the same scenario (after reset) produce identical scores and tier dates.
- **SC-007**: The demo replay can be driven from start to Action in under 90 seconds of stage time using seek and autoplay.

## Assumptions

- Exact dates, districts and onsets in clarify.md are recommendations to be verified against NDMA/PDMA situation reports.
- Forecast archives cover Buner 2025 but may not cover 2021–2022; those use labelled proxies (FR3 clarify).
- The synthetic beneficiary register (FR10) is shared reference data; replays allocate and pay against it with scenario-scoped rows.
- The demo laptop has the repository with committed caches and artifacts; no network is needed except for an optional live WhatsApp approval.
- Payments in replay always use the simulated rail.

## Traceability

| Item | How FR5 covers it |
|---|---|
| UC05 step 4 (replay slider) | US6 |
| NFR3 | US3, SC-001, SC-002 |
| NFR12 / Principle II | US4, FR5-007, FR5-008, SC-004 |
| NFR13 / Principle VIII | FR5-015, proxy labelling |
| NFR7 / Principle III | FR5-010 (no audit deletes, new chain) |
| NFR1 | SC-005 |
| Q5, Q6, Q14 (live replay demo, three events, 3 minutes) | US1, US6, SC-007 |
| D1 | Precomputed Sentinel-1 maps loaded into the scenario |
| D3 | Live approval uses the real WhatsApp/dashboard channels |
| D4 | Replay fires tiers through FR7 unchanged |
| D6 | Escalation not triggered in simulated mode |
