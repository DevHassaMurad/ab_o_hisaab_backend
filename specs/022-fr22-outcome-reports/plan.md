# Implementation Plan: FR22 Outcome Reports

**Branch**: `022-fr22-outcome-reports` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/022-fr22-outcome-reports/spec.md`

## Summary

Roadmap feature behind `FR22_OUTCOME`. The **MVP stub** is a flag-guarded `OutcomeStoryService` returning `[]`, routes answering `FEATURE_DISABLED`, and donor-scoped schemas with a contract test. The **roadmap build** adds a scheduled job that starts a three-question WhatsApp survey 7 days after a beneficiary's last PAID payment in an event, a reply handler called by FR15's webhook, expiry, k-anonymous aggregation and template stories in Urdu and English for FR19.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, APScheduler (via `app/core/scheduler.py`), Twilio through FR14's `AlertService`

**Storage**: PostgreSQL 16; table `outcome_report` (foundation) + migration 0022 (roadmap build)

**Testing**: pytest, httpx TestClient, freezegun-style `ReplayClock` from the foundation for time travel

**Target Platform**: Linux container

**Project Type**: web-service + in-process scheduled job

**Performance Goals**: job tick < 30 s for 50 000 paid beneficiaries; aggregates p95 < 500 ms

**Constraints**: no PII in aggregates, stories, logs or audit; Urdu first; idempotent job and reply handling (`provider_message_id`)

**Scale/Scope**: ≤ 50 000 surveys per season in the pilot

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| III Traceability | `OUTCOME_SURVEY_SENT`, `OUTCOME_REPORT_COMPLETED`, `OUTCOME_REPORT_EXPIRED` in the same transaction | ✅ |
| VI Privacy | `DonorScope` schemas; k = 5 suppression; individual reports 403 for donors, never show names/phones; no free text | ✅ |
| VIII Honest prototype | `simulated` on reports, aggregates and stories; simulation script labels everything | ✅ |
| IX Config over code | Delay, expiry, window, k, sample rate, question set version in settings/YAML | ✅ |
| X Demo-first | Off by default; MVP stub only; no new infra (same scheduler) | ✅ |
| XI Tests | Donor no-PII contract test written first (stub phase) | ✅ |
| NFR10 | Urdu first, one digit per answer, one question per message | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| WhatsApp, digit replies, 3 questions | Same pattern as UC10 receipts; low literacy; easy to aggregate | Long forms, web links (low completion), voice (needs transcription) |
| Survey after the **last** PAID payment of the event, +7 days | One survey per event; recall still fresh | Survey after every payment (spam, duplicates) |
| Wait while a payment notice is unanswered | No ambiguity for FR15's `1`/`2` routing | Prefixed answers ("S1") (harder for users) |
| k-anonymity k = 5 at district × type × event | Standard small-cell suppression; districts are large | Differential privacy (overkill for the pilot); tehsil cells (re-identification risk) |
| Template stories | Same as D2: explainable, no hallucinated numbers | LLM stories (needs FR13 guardrails; later) |
| Stub returns `[]` | FR19 already has curated simulated stories | Stub returning fake stories (would mix two simulated sources) |

## Data model slice

| Entity | Fields | Access |
|---|---|---|
| `OutcomeReport` | `beneficiary_id`, `event_id`, `channel`, `answers`, `received_at` (+ proposed fields) | read/write (roadmap) |
| `Payment` | `beneficiary_id`, `status`, `paid_at`, `allocation_id`, `scenario_id` | read |
| `Allocation` → `FundRelease` → `TriggerEvent` | `event_id`, `district_id` | read |
| `Alert` | via `AlertService.enqueue`; read latest `PAYMENT_NOTICE` and its `Confirmation` | read |
| `Beneficiary` | `type`, `preferred_language`, `phone_valid`, `simulated`, `register_ref`, `tehsil_id` | read |
| `DonorContribution` | `donor_id`, `fund_id` | read (donor scoping) |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

For `OutcomeReport` (data-model.md §5), merged by the foundation owner before the roadmap build:

| Field | Type | Notes |
|---|---|---|
| `payment_id` | FK Payment, nullable | Last PAID payment that started the clock |
| `status` | enum `SENT`, `IN_PROGRESS`, `COMPLETED`, `EXPIRED` | |
| `survey_version` | str | `outcome-v1` |
| `current_question` | int | 1–3; 4 = done |
| `invalid_attempts` | int | Re-ask counter for the current question (max 1 re-ask) |
| `sent_at` | timestamptz | Survey start |
| `last_message_at` | timestamptz | For expiry |
| `received_at` | timestamptz, **nullable** | Change: null until completed |
| `last_provider_message_id` | str, nullable | Idempotency for duplicate webhooks |
| `simulated` | bool | 🧪 |
| `scenario_id` | FK, nullable | Shared rule 2 |
| *unique* | `(beneficiary_id, event_id, scenario_id)` | One survey per beneficiary per event |

`answers` JSONB shape:

```json
{
  "version": "outcome-v1",
  "q1": {"key": "evacuated", "value": 1, "code": "YES", "raw": "۱", "at": "2022-09-02T08:14:03Z"},
  "q2": {"key": "spent_on", "value": 1, "code": "FOOD", "raw": "1 khana", "at": "2022-09-02T08:16:40Z"},
  "q3": {"key": "helped", "value": null, "code": null, "raw": "ok", "at": "2022-09-02T08:20:11Z", "invalid": true}
}
```

## Survey content (`outcome-v1`)

Template keys for FR14 (Urdu first, then English, one message each):

| key | Text |
|---|---|
| `outcome_intro` | السلام علیکم۔ آب و حساب: سیلاب سے پہلے ملنے والی رقم کے بارے میں 3 مختصر سوال۔ ہر سوال کا جواب صرف ایک نمبر سے دیں۔ / Aab o Hisaab: 3 short questions about the money you received before the flood. Reply with one number. |
| `outcome_q1_household` | کیا آپ پانی آنے سے پہلے اپنے گھر والوں کو محفوظ جگہ لے گئے؟ 1 ہاں 2 نہیں / Did you move your family to a safe place before the water came? 1 Yes 2 No |
| `outcome_q1_shopkeeper` | کیا آپ نے پانی آنے سے پہلے دکان کا مال بچا لیا؟ 1 سارا 2 کچھ 3 نہیں / Did you protect your shop's goods before the water came? 1 All 2 Some 3 None |
| `outcome_q2` | آپ نے رقم زیادہ تر کس چیز پر خرچ کی؟ 1 کھانا 2 سفر یا نقل مکانی 3 رہائش 4 دوا 5 دکان یا مال 6 کچھ اور / What did you spend most of the money on? 1 Food 2 Transport or moving 3 Shelter 4 Medicine 5 Shop or goods 6 Something else |
| `outcome_q3` | اس رقم سے آپ کو کتنی مدد ملی؟ 1 بہت 2 تھوڑی 3 بالکل نہیں / How much did this money help? 1 A lot 2 A little 3 Not at all |
| `outcome_reask` | براہ کرم فہرست میں سے صرف ایک نمبر بھیجیں۔ / Please reply with one number from the list. |
| `outcome_thanks` | شکریہ! آپ کے جوابات سے امداد بہتر بنائی جائے گی۔ آپ کا نام کسی کو نہیں دکھایا جائے گا۔ / Thank you! Your answers help improve support. Your name will not be shown to anyone. |

Codes: Q1 household `YES`/`NO`; Q1 shopkeeper `ALL`/`SOME`/`NONE`; Q2 `FOOD`/`TRANSPORT`/`SHELTER`/`MEDICINE`/`SHOP_GOODS`/`OTHER`; Q3 `A_LOT`/`A_LITTLE`/`NOT_AT_ALL`. In each message the Urdu block comes first, then a blank line, then English, with options on separate lines.

## Story templates

| Type | Urdu | English |
|---|---|---|
| Household headline | {district}: {pct}% گھرانے بروقت محفوظ جگہ پہنچے | {pct}% of households moved to safety in {district} |
| Household body | {district} میں جواب دینے والے {n} گھرانوں میں سے {k} ({pct}%) پانی آنے سے پہلے اپنے گھر والوں کو محفوظ جگہ لے گئے۔ زیادہ تر نے رقم {top_ur} پر خرچ کی۔ | In {district}, {k} of {n} households who answered ({pct}%) moved their families to safety before the water came. Most spent the money on {top_en}. |
| Shopkeeper headline | {district}: {pct}% دکانداروں نے مال بچایا | {pct}% of shopkeepers protected their goods in {district} |
| Shopkeeper body | {district} میں جواب دینے والے {n} دکانداروں میں سے {k} ({pct}%) نے پانی آنے سے پہلے اپنا سارا یا کچھ مال بچا لیا۔ {helped_pct}% نے کہا کہ رقم سے بہت مدد ملی۔ | In {district}, {k} of {n} shopkeepers who answered ({pct}%) protected all or some of their goods before the water came. {helped_pct}% said the money helped a lot. |

A story is produced only for a cell that is not suppressed for the question it cites.

## API contracts

Router `app/api/v1/outcomes.py`, tag **Reporting**. When the flag is off, every route returns `404` problem JSON `{"code": "FEATURE_DISABLED"}`.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/v1/outcomes/stories?district_id=` | PDMA_OFFICER, ADMIN, DC (own), DONOR (linked funds) | Story cards |
| GET | `/api/v1/outcomes/aggregates?district_id=&event_id=&type=` | PDMA_OFFICER, ADMIN, DC (own), DONOR (linked funds) | k-anonymous aggregates |
| GET | `/api/v1/outcomes/reports?district_id=&status=&event_id=&page=` | PDMA_OFFICER, ADMIN, DC (own) | Individual reports, no PII |
| GET | `/api/v1/outcomes/progress?district_id=` | PDMA_OFFICER, ADMIN, DC (own) | Counts by status, response rate |
| POST | `/api/v1/outcomes/surveys/run` | ADMIN | Run the job tick now (demo) |

DONOR on `/reports` or `/progress` → `403 INDIVIDUAL_DATA_DENIED` (mirrors UC11-3a). DONOR asking for a district without a linked fund → `403 FUND_NOT_LINKED`.

**GET `/outcomes/aggregates?district_id=…`** (`DonorScope`)

```json
{
  "items": [
    {
      "district": {"id": "…", "name": "Dadu"},
      "beneficiary_type": "HOUSEHOLD",
      "suppressed": false,
      "event": {"id": "…", "period": "2022-08"},
      "n_sent": 52, "n_completed": 40, "n_expired": 12, "response_rate_pct": 76.9,
      "questions": {
        "evacuated": {"n": 40, "suppressed": false, "shares_pct": {"YES": 82.5, "NO": 17.5}},
        "spent_on": {"n": 39, "suppressed": false,
                     "shares_pct": {"FOOD": 46.2, "TRANSPORT": 30.8, "SHELTER": 10.3, "MEDICINE": 7.7, "SHOP_GOODS": 0.0, "OTHER": 5.1}},
        "helped": {"n": 38, "suppressed": false, "shares_pct": {"A_LOT": 71.1, "A_LITTLE": 23.7, "NOT_AT_ALL": 5.3}}
      },
      "simulated": true
    },
    {
      "district": {"id": "…", "name": "Buner"}, "beneficiary_type": "SHOPKEEPER", "event": {"id": "…", "period": "2025-08"},
      "n_sent": null, "n_completed": null, "n_expired": null, "response_rate_pct": null, "suppressed": true,
      "questions": {"stock_protected": {"n": null, "suppressed": true, "shares_pct": null},
                    "spent_on": {"n": null, "suppressed": true, "shares_pct": null},
                    "helped": {"n": null, "suppressed": true, "shares_pct": null}},
      "simulated": true
    }
  ],
  "k_anonymity": 5
}
```

`n_sent`/`n_completed` counts below k are themselves replaced by `null` with `suppressed=true` when `n_completed < k` (counts can identify people in small groups), as in the Buner example above (3 completed). Response rate is shown only when `n_sent ≥ k`.

**GET `/outcomes/stories?district_id=…`** (`DonorScope`)

```json
{
  "items": [
    {"district": "Dadu", "beneficiary_type": "HOUSEHOLD", "event_period": "2022-08",
     "headline_ur": "دادو: 83% گھرانے بروقت محفوظ جگہ پہنچے",
     "headline_en": "83% of households moved to safety in Dadu",
     "body_ur": "دادو میں جواب دینے والے 40 گھرانوں میں سے 33 (83%) …",
     "body_en": "In Dadu, 33 of 40 households who answered (83%) moved their families to safety before the water came. Most spent the money on food.",
     "n_respondents": 40, "simulated": true}
  ]
}
```

**GET `/outcomes/reports?district_id=…&status=COMPLETED`** (officer)

```json
{
  "items": [
    {"id": "…", "register_ref": "SYN-PK60501-H-00042", "beneficiary_type": "HOUSEHOLD", "event_id": "…",
     "status": "COMPLETED", "current_question": 4, "sent_at": "2022-09-02T05:00:00Z", "received_at": "2022-09-02T08:20:11Z",
     "answers": {"q1": "YES", "q2": "FOOD", "q3": null}, "simulated": true}
  ],
  "total": 40, "page": 1, "page_size": 50
}
```

**GET `/outcomes/progress?district_id=…`** → `{"sent": 52, "in_progress": 0, "completed": 40, "expired": 12, "response_rate_pct": 76.9, "simulated_share": 1.0}`

**POST `/outcomes/surveys/run`** → `{"started": 14, "expired": 3, "skipped_pending_notice": 2, "skipped_outside_window": 0}`

## Audit events

| event_type | entity | payload |
|---|---|---|
| `OUTCOME_SURVEY_SENT` | OutcomeReport | `beneficiary_id`, `event_id`, `payment_id`, `survey_version`, `simulated` |
| `OUTCOME_REPORT_COMPLETED` | OutcomeReport | `answered`, `invalid`, `answers_sha256` |
| `OUTCOME_REPORT_EXPIRED` | OutcomeReport | `answered`, `last_question` |

## Scheduled jobs

| Job | Schedule | Behaviour |
|---|---|---|
| `outcome_survey_tick` (`app/jobs/outcome_survey.py`) | every 15 min (`OUTCOME_JOB_INTERVAL_MIN`) | No-op if flag off. `expire_stale()` then `schedule_due_surveys()` (inside the send window only). Idempotent by the unique key |

## Configuration

| Key | Default |
|---|---|
| `FLAGS.FR22_OUTCOME` | `false` |
| `OUTCOME_SURVEY_DELAY_DAYS` | 7 |
| `OUTCOME_SURVEY_EXPIRY_HOURS` | 72 |
| `OUTCOME_PENDING_NOTICE_WAIT_HOURS` | 48 |
| `OUTCOME_SEND_WINDOW` | `09:00-19:00` Asia/Karachi |
| `OUTCOME_K_ANON` | 5 |
| `OUTCOME_SURVEY_SAMPLE_RATE` | 1.0 |
| `OUTCOME_JOB_INTERVAL_MIN` | 15 |
| `OUTCOME_SIM_DISTRIBUTIONS` | `data/seeds/outcome_simulation.yaml` (roadmap) |

## Project Structure

### Documentation (this feature)

```text
specs/022-fr22-outcome-reports/
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
├── api/v1/outcomes.py                   # stub + roadmap routes
├── schemas/outcomes.py                  # OutcomeStoryOut, OutcomeAggregateOut (DonorScope), OutcomeReportOut, ProgressOut
├── services/outcomes/
│   ├── service.py                       # OutcomeStoryService (flag-guarded)
│   ├── questions.py                     # QUESTIONS_V1, parse_answer
│   ├── survey.py                        # schedule_due_surveys, handle_reply, expire_stale, has_open_survey
│   ├── aggregate.py                     # aggregate_outcomes
│   └── stories.py                       # render_story
└── jobs/outcome_survey.py               # register outcome_survey_tick
alembic/versions/0022_fr22_outcome_reports.py
scripts/simulate_outcome_responses.py
data/seeds/outcome_simulation.yaml
tests/unit/outcomes/{test_questions_parse,test_aggregate_kanon,test_stories}.py
tests/integration/outcomes/{test_flag_off,test_survey_flow,test_expiry}.py
tests/contract/test_outcomes_donor_scope.py
```

**Structure Decision**: single project; new router `outcomes.py` (Reporting tag) kept separate from FR19's `reports.py` so FR19 is not blocked.

## Complexity Tracking

None.
