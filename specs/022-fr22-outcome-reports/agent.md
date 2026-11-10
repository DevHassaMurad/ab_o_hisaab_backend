# Agent Brief: FR22 Outcome Reports

## Owner role

**Backend & alerts**

## Mission

After support is paid, ask each beneficiary a short WhatsApp survey (Urdu first) about what the money helped them do, store the answers as `OutcomeReport`, and turn them into anonymised, aggregated outcome stories for the donor report (FR19, UC11-4). This is a **roadmap** FR behind the flag `FR22_OUTCOME`: the MVP ships only a stub so FR19 can call it safely and keep using curated simulated stories.

## Scope

**MVP stub (behind `FR22_OUTCOME`, default off)**
- `OutcomeStoryService.stories(...)` and `.aggregates(...)` that return empty results when the flag is off.
- Routes registered but answering `404 FEATURE_DISABLED` when the flag is off.
- Donor-scoped response schemas (`DonorScope`) and the contract test proving no beneficiary-level fields (NFR8).

**Roadmap build**
- Survey scheduling N days (default 7) after a beneficiary's last `PAID` payment in an event; Urdu-first WhatsApp questions via FR14; replies routed from FR15's webhook; state machine `SENT → IN_PROGRESS → COMPLETED | EXPIRED`.
- Answer parsing (Western, Urdu and Arabic-Indic digits), one re-ask on an invalid reply, expiry after 72 h.
- Aggregation per district × beneficiary type × event with k-anonymity (k = 5); template stories in Urdu and English.
- Replay support: simulated responses (labelled) so the demo can show real-pipeline stories.
- Officer monitoring list (no names or phones).

**Out**
- Free-text answers, voice notes, photos (privacy risk; roadmap+).
- SMS or field-agent surveys (FR16 handles fallback for payment receipts only).
- Rendering stories in the donor PDF (FR19).

## Read first

1. `specs/_authoring-guide.md` (FR22, FR19, FR14, FR15 facts)
2. `.specify/memory/constitution.md` (Principles III, VI, VIII, X; NFR8, NFR10)
3. `specs/000-foundation/data-model.md` §5 `OutcomeReport`, §7 `Alert`, `Confirmation`
4. `specs/000-foundation/plan.md` (`flags.py`, `DonorScope`, API conventions)
5. This folder: `spec.md`, `clarify.md`, `plan.md`
6. `specs/014-fr14-whatsapp-alerts/plan.md`, `specs/015-fr15-receipt-confirmation/plan.md`, `specs/019-fr19-donor-report/plan.md`

## Depends on / Blocks

| Direction | FR | Reason |
|---|---|---|
| Depends on | FR17 `017-fr17-payment-tracking` | `Payment.status=PAID`, `paid_at` start the survey clock |
| Depends on | FR14 `014-fr14-whatsapp-alerts` | `AlertService.enqueue(purpose=OUTCOME_SURVEY)`; sandbox/allowlist rules |
| Depends on | FR15 `015-fr15-receipt-confirmation` | Inbound webhook routes survey replies to `handle_reply` |
| Depends on | FR10 `010-fr10-beneficiary-register` | Type, preferred language, `phone_valid`, `simulated` |
| Depends on | FR20 `020-fr20-audit-log` | Audit of survey sent/completed/expired |
| Depends on | FR5 `005-fr05-event-replay` | `scenario_id` scoping; simulated responses in replays |
| Blocks | FR19 `019-fr19-donor-report` | Real outcome stories replace curated simulated stories when the flag is on |

## Interfaces

**Consumes**
- `AlertService.enqueue(db, *, recipient, purpose="OUTCOME_SURVEY", template_key, context, related)`
- `AuditService.record(...)`, `Clock`, `ScenarioContext`, `flags.is_enabled("FR22_OUTCOME")`
- FR15 digit normaliser (expected `app.services.messaging.parse.normalise_digits`; confirm in `015-fr15-receipt-confirmation/plan.md`)

**Provides**
- `app.services.outcomes.service.OutcomeStoryService.stories(db, *, district_ids, scenario_id=None) -> list[OutcomeStoryOut]`
- `app.services.outcomes.service.OutcomeStoryService.aggregates(db, *, district_ids, event_id=None, scenario_id=None) -> list[OutcomeAggregateOut]`
- `app.services.outcomes.survey.handle_reply(db, *, beneficiary_id, body, provider_message_id) -> bool` (FR15 calls it; `False` = not a survey reply)
- `app.services.outcomes.survey.has_open_survey(db, beneficiary_id) -> bool` (FR15 routing)
- Job `outcome_survey_tick` registered with the scheduler
- REST: `/api/v1/outcomes/*` (plan.md)

## Files you own

```text
app/services/outcomes/__init__.py
app/services/outcomes/service.py           # OutcomeStoryService (stub + real)
app/services/outcomes/questions.py         # survey-v1 questions, EN/UR texts, parse_answer
app/services/outcomes/survey.py            # schedule_due_surveys, handle_reply, expire_stale, has_open_survey
app/services/outcomes/aggregate.py         # aggregate_outcomes with k-anonymity
app/services/outcomes/stories.py           # render_story (EN/UR templates)
app/api/v1/outcomes.py
app/schemas/outcomes.py
app/jobs/outcome_survey.py
alembic/versions/0022_fr22_outcome_reports.py
scripts/simulate_outcome_responses.py
tests/unit/outcomes/test_questions_parse.py
tests/unit/outcomes/test_aggregate_kanon.py
tests/unit/outcomes/test_stories.py
tests/integration/outcomes/test_flag_off.py
tests/integration/outcomes/test_survey_flow.py
tests/integration/outcomes/test_expiry.py
tests/contract/test_outcomes_donor_scope.py
```

## Guardrails

| Principle | How it applies |
|---|---|
| III Traceability | `OUTCOME_SURVEY_SENT`, `OUTCOME_REPORT_COMPLETED`, `OUTCOME_REPORT_EXPIRED` audited in the same transaction; payloads hold ids, no answers text, no PII |
| VI / NFR8 Privacy | Donor schemas inherit `DonorScope`; cells with fewer than 5 respondents are suppressed; stories never name people or tehsils with small counts; contract test mandatory |
| VIII Honest prototype | Replay/simulated responses carry `simulated=true`; stories built from them are labelled simulated |
| X Demo-first | Flag off by default; stub is tiny; FR19 never breaks if FR22 is missing |
| NFR10 | Urdu first, numeric replies, one short question per message |
| XI | Donor aggregation is a listed money-adjacent path (no PII leaks): contract test written first |

## Definition of Done

**MVP stub**
- [ ] Flag `FR22_OUTCOME` read from `app/core/flags.py`; off by default
- [ ] `OutcomeStoryService` returns `[]` when off; FR19 can import and call it
- [ ] `/outcomes/*` routes answer `404 FEATURE_DISABLED` when off
- [ ] `tests/contract/test_outcomes_donor_scope.py` and `tests/integration/outcomes/test_flag_off.py` pass

**Roadmap build**
- [ ] Migration 0022 applied after the foundation owner merges the proposed fields
- [ ] Survey sent once per beneficiary per event, N days after the last PAID payment, inside the send window
- [ ] Replies parsed (incl. `۱`, `١`), re-ask once, completion and expiry audited
- [ ] Aggregates with k = 5 suppression; stories EN/UR; donor contract test passes
- [ ] Simulated responses script works for `sindh-2022` and `buner-2025`

## Hand-off notes

- **FR15**: routing order for an inbound beneficiary message: (1) DC commands, (2) the latest open payment notice without a `Confirmation`, (3) `has_open_survey(...)` → `handle_reply(...)`, (4) otherwise `UNPARSEABLE`. FR22 never sends a survey while a payment notice is still waiting for a reply, so (2) and (3) never compete.
- **FR14**: add template keys `outcome_intro`, `outcome_q1_household`, `outcome_q1_shopkeeper`, `outcome_q2`, `outcome_q3`, `outcome_reask`, `outcome_thanks` (texts in plan.md). Business-initiated surveys need pre-approved WhatsApp templates in production. Apply `DEMO_PHONE_ALLOWLIST` to simulated beneficiaries.
- **FR19**: call `OutcomeStoryService.stories(db, district_ids=<donor's funds' districts>)`. If it returns `[]` (flag off or no data), show your curated simulated stories. Each story has `simulated` and `n_respondents`.
- **Foundation**: `app/core/flags.py` lists `FR22_OUTCOME`; this spec uses `FR22_OUTCOME` (guide). Please align to one name.
