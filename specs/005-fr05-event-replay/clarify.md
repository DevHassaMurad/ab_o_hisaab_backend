# Clarifications: FR5 Event Replay

## Clarifications

### Session 2026-11-10

- Q: **Exact replay windows and districts?** → A (all dates **verify** against NDMA/PDMA situation reports before the caches are frozen):

  | Key | Window (simulated run dates) | Spin-up | Districts (resolved by name + province at seed) | Hero | Reference onset | Expected | Min lead (target) |
  |---|---|---|---|---|---|---|---|
  | `sindh-2022` | 2022-08-01 → 2022-09-10 (41 days) | 2022-07-02 → 2022-07-31 | Dadu, Qambar Shahdadkot, Larkana, Jamshoro, Khairpur (Sindh) | Dadu | 2022-08-22 | TRIGGER | 3 days (7) |
  | `buner-2025` | 2025-08-01 → 2025-08-25 (25 days) | 2025-07-02 → 2025-07-31 | Buner, Swat, Shangla (KP) | Buner | 2025-08-15 | TRIGGER | 1 day (1–2) |
  | `control-2021` | 2021-08-01 → 2021-09-10 (41 days) | 2021-07-02 → 2021-07-31 | Same five Sindh districts as `sindh-2022` | — | — | NO_TRIGGER | — |

  *Why*: Sindh windows start two weeks before the mid-August 2022 rain spells so the slider shows a calm start, a climb through Watch/Warning/Action, and the early-September peak (Manchar/Dadu). The five districts are Dadu and its rain-flooded neighbours on the right bank and centre of Sindh. Buner's window brackets the 15 Aug 2025 cloudburst with the neighbouring districts hit the same week. The control uses the **same districts and calendar window** in a normal year, which is the strongest no-false-alarm test (same geography, same season, same thresholds). Reference onset for Dadu is a conservative "rain-flooding widespread in the district" date; the later Indus/Manchar peak is not used, because that would flatter lead time.
- Q: Which P-codes? → A: Not hard-coded. `data/replay/scenarios.yaml` lists names and province; `ensure_scenarios()` resolves each to exactly one `District` (case-insensitive) and fails loudly otherwise. P-codes are written into `manifest.json` at cache build time.
- Q: What does "trigger" mean for the backtest? → A: `TRIGGER` passes when the hero district has ≥ 1 `FundRelease` in the run **and** the first one's run date ≤ reference onset − `min_lead_days`. Tiers reached, per-tier first dates and lead against `stretch_lead_days` are reported but not pass/fail. *Why*: NFR3 says "catch the event"; a catch on the day of the flood is not anticipatory (Principle I), so a minimum lead is required; the stretch target (7 days Sindh, Q10) is a goal, not a gate, given proxy forecasts.
- Q: What does "no trigger" mean for the control? → A: Zero `FundRelease` rows in the scenario run **and** every district `RiskIndex.score` < that district's Watch `min_score` on every simulated day. The report shows the maximum score and the margin to 40.
- Q: Which forecast rainfall is used in replays? → A: FR3's provenance ladder: Open-Meteo Previous Runs (`ARCHIVED_FORECAST`) → Historical Forecast (`ARCHIVED_SHORT_LEAD_PROXY` for leads ≥ 2) → observed IMERG (`PERFECT_FORECAST_PROXY`). Expected (verify coverage): buner-2025 archived; sindh-2022 short-lead proxy or perfect proxy; control-2021 perfect proxy. The manifest counts rows per provenance; the state, timeline and backtest report show it; the explanation adds the proxy notice. *Why*: Principle VIII; the pitch must say "best-case forecast" where it is one.
- Q: Is a perfect-proxy control test meaningful? → A: Yes, and it is the harder direction for false alarms: with perfect knowledge of the 2021 rain, the model still must not fire. Trigger tests with a perfect proxy are easier, so the report and pitch label them.
- Q: How does the replay avoid lookahead? → A: Each source's replay reader returns only data available on the simulated date: NASA POWER up to `sim − REPLAY_POWER_LAG_DAYS` (FR1, default 2), IMERG up to `sim − REPLAY_IMERG_LAG_DAYS` (FR2), forecast rain only rows with `issue_date == sim` (FR3).
- Q: How do replays get 30 days of history for the 30-day windows? → A: At start, FR5 runs every registered ingestor for the spin-up range under `ReplayClock(start_date − 1)` and the scenario context. FR3 does not score spin-up days.
- Q: Which funds does a replay use? → A: Scenario-scoped funds and tiers created at start via FR6 `create_fund_with_tiers`, with defaults 40/60/80, 10/30/60, 70/30, cap 50 000, and amounts from `scenarios.yaml` (`allocated_pkr` 50 000 000 per district, matching FR6's demo). Live funds are never touched.
- Q: How is DC approval handled? → A: `approval_mode` chosen at start: `AUTO_SIM` (default; backtests) creates the decision via FR8 with `channel=REPLAY_SIM`, `simulated=true`, `decision=APPROVED`, approver = the seeded DC of that district if one exists (else null), actor `replay-sim`, reason "Replay simulation: automatic approval"; `LIVE_WHATSAPP` and `DASHBOARD` pause the replay (`AWAITING_APPROVAL`) until FR8 records a real decision (channel `WHATSAPP`/`DASHBOARD`, `simulated=false`; the money is still simulated). *Why*: Principle II forbids skipping the record; D3 channels are reused unchanged on stage.
- Q: Should simulated approvals ever reject? → A: No in the MVP (`AUTO_SIM` approves). A rejection path is exercised in FR8's own tests and in `LIVE_WHATSAPP`.
- Q: What happens to the 2-hour escalation (D6) in replay? → A: Not started for `AUTO_SIM` (decided in the same step). In live modes FR8's wall-clock timer applies (it will not fire within a 3-minute demo).
- Q: **Reset vs. append-only audit.** → A: Follow FR20: never delete audit rows. Reset (1) stops autoplay, (2) deletes the scenario's rows from every operational table (children first), (3) increments `ReplayScenario.run_no`, which gives a new chain `replay:{scenario_id}:{run_no}`, (4) sets status `IDLE`, sim date null. Old chains stay verifiable with `?run_no=`. Operational tables are mutable, so deleting replay rows does not break Principle III; the audit rows that described them remain.
- Q: Why not create a new `ReplayScenario` row per run instead of deleting? → A: Rejected: `X-Scenario-Id` changes on every run (frontend churn), and every table would keep growing during rehearsals. FR20 already designed `run_no`.
- Q: What does seek backwards do? → A: 409 `REPLAY_SEEK_BACKWARD` unless `reset: true`, then reset + start (same approval mode) + step to the date. FR7 relies on this (it ignores older dates).
- Q: How do async jobs (payments, alerts) finish within a simulated day? → A: After the pipeline, the step drains the jobs listed in `REPLAY_DRAIN_JOBS` (default `payments.dispatch`, `messaging.dispatch`) with the scheduler's `run_now` inside the scenario context, so the day's state is complete before the next day.
- Q: Clock semantics? → A: `ReplayClock.today()` = sim date; `now()` = sim date at `PIPELINE_TIME_UTC` (01:00) + wall time elapsed since the step started (monotonic within a step). Audit `timestamp` stays wall clock (FR20 decision).
- Q: Who can drive and who can watch? → A: Drive (start/step/seek/reset/play/pause): `PDMA_OFFICER`, `ADMIN`. Watch (list/state/timeline/backtest): those plus `DC` (own district rows only) and `DONOR` (district aggregates only, `DonorScope` schema with no beneficiary fields).
- Q: Concurrency? → A: Session advisory lock `pg_try_advisory_lock(5005, hashtext(key))` per step; busy → 409 `REPLAY_BUSY`. Different scenarios may run at once.
- Q: Where do backtests run? → A: `tests/backtest/`, marker `backtest`, excluded from the default `pytest` run (`-m "not backtest"` in `pyproject.toml`), run in CI on merges to `main` and before the demo. Fast engine tests use the `tests/fixtures/replay/mini/` scenario.
- Q: Is the register shared or per scenario? → A: Shared reference data (FR10 decision); replay allocations and payments are scenario rows. A replay flagging `phone_valid=false` would leak into live; FR14's dry-run/allowlist means replay sends to synthetic numbers never fail for real, so this does not happen in practice.

## Resolved by constitution decisions

- **Principle II / D3**: approval record always created; live demo uses the real channels.
- **Principle III**: no audit deletes → chain per run (FR20).
- **Principle VIII**: replay data, approvals and proxies labelled.
- **D1**: precomputed Sentinel-1 maps for the two trigger events are part of the cache.
- **D4**: tiers fire through FR7 unchanged; Action waits for approval.
- **D6**: escalation goes to the PDMA officer; not used in `AUTO_SIM`.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Windows, districts and reference onsets | As in the table above (verify) | Changes caches, backtest expectations and demo script | Data & ML lead + pitch lead |
| 2 | Minimum lead for pass | Sindh 3 days, Buner 1 day | 7 days for Sindh may fail with proxy forecasts; 0 days would allow "reactive" passes | Data & ML lead |
| 3 | Add a flash-mode control year (e.g. Buner/Swat Aug 2023 or 2024, verify it was flood-free) | Not in MVP | Without it, the no-false-alarm claim only covers slow-onset mode | Team lead |
| 4 | Default approval mode for the stage | `AUTO_SIM` for rehearsal, `LIVE_WHATSAPP` for the hero Dadu Action only | Live mode adds network risk on stage | Frontend & pitch |
| 5 | Retain old replay runs' audit chains forever | Yes (small) | A purge would need a superuser script outside Principle III | Backend lead (FR20) |
| 6 | Replay fund size | PKR 50 M per district | Changes amounts shown on stage only | PDMA product owner |
| 7 | Rename `ReplayScenario.current_date` | **Resolved**: renamed to `sim_date` in data-model v1.1 | — | Foundation owner |
