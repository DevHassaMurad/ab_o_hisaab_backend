# Implementation Plan: FR8 DC Approval of Action-Tier Releases

**Branch**: `008-fr08-dc-action-approval` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/008-fr08-dc-action-approval/spec.md`

## Summary

FR7 calls `request_approval(release)` when Action fires. FR8 creates one `Approval` row (PENDING), generates a 24 h WhatsApp approval code, and queues an `APPROVAL_REQUEST` to the DC. The DC decides either on the dashboard (request a 10-minute OTP, then approve) or on WhatsApp (`APPROVE <code>` / `REJECT <reason>`, routed by FR15 to `handle_dc_whatsapp_command`). Both paths go through `decide()`, which locks the row, records the channel, audits, and either runs FR7's `run_release_pipeline` or marks the release `REJECTED` and notifies officers. An APScheduler job escalates PENDING approvals after 2 h (D6). Replays simulate decisions with `channel=REPLAY_SIM` (FR5 calls `simulate_pending_approvals` after each step's trigger evaluation) unless configured for a live WhatsApp approval.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, passlib[bcrypt], APScheduler, `secrets`, `unicodedata`

**Storage**: PostgreSQL 16 (`approval`, reads `fund_release`, `risk_index`, `flood_forecast`, `satellite_flood_map`, `beneficiary`, `user`)

**Testing**: pytest, freezegun-style `FakeClock`, `RecordingAlertService`, Postgres test DB

**Target Platform**: Linux container

**Project Type**: web-service + interval job

**Performance Goals**: detail view p95 < 500 ms; WhatsApp handler < 1 s (bcrypt ≈ 0.25 s per comparison; ≤ 2 open approvals per DC)

**Constraints**: No plaintext codes stored; UTC; first decision wins; idempotent on `provider_message_id`

**Scale/Scope**: ≤ 68 DCs; ≤ 1 open approval per district per scenario

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| II Human authority | `decide()` checks approver is an active `DC` with `district_id == release district` unless `simulated=True`; `simulate_replay_decision` raises for `scenario_id IS NULL`; FR7 guard blocks allocation before `APPROVED`. Officer/admin approve → 403 `APPROVER_NOT_DC` | ✅ |
| III Traceability | 6 audit event types, in the same transaction; failed-attempt audits committed even when the request fails | ✅ |
| IV Explainable | Detail view assembles the stored score inputs and explanation; decision audit stores minutes-to-decide and channel | ✅ |
| VI Privacy | DC district scope; masked phones; bcrypt hashes; codes never logged | ✅ |
| VII Degrade | Detail view shows `is_fallback` / `is_stale_input` flags so the DC knows | ✅ |
| VIII Honest | `REPLAY_SIM` + `simulated=true` | ✅ |
| X Simplicity | One interval job; one service module | ✅ |
| XI Tests | Gating, OTP, code, escalation tests first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Separate WhatsApp code (24 h) and dashboard OTP (10 min) | The request message is read later than 10 min; the OTP must be fresh for the dashboard | One code for both: either too short for WhatsApp or too long for an OTP |
| bcrypt for both codes | Constitution stack; slow hash resists offline guessing of 6 digits | SHA-256: 10^6 space brute-forced instantly |
| Single `decide()` for all channels | One record, one audit shape (D3) | Per-channel logic: drift |
| Escalation by 5-minute interval job | Simple, in-process (Principle X) | Per-approval scheduled job: lost on restart unless persisted |
| Replay escalation driven by FR5 step | Replay time ≠ wall time | Wall-clock job on replay rows: everything instantly overdue |
| Reply text returned to FR15 | Lets FR15 answer in the same webhook (TwiML) with no extra Alert row | FR8 enqueuing a reply Alert: duplicates FR15 delivery logic |

## Approval state machine

```text
request_approval ──▶ PENDING ──DC approves──▶ APPROVED (terminal) ──▶ FR7 run_release_pipeline
                       │    └──DC rejects───▶ REJECTED (terminal) ──▶ FundRelease REJECTED, officers notified
                       │ 2 h, no decision (or no DC)
                       ▼
                    ESCALATED ──DC approves/rejects──▶ APPROVED / REJECTED
```

`APPROVAL_TRANSITIONS = {"PENDING": {"APPROVED", "REJECTED", "ESCALATED"}, "ESCALATED": {"APPROVED", "REJECTED"}}`.

## Data model slice

| Entity | Read | Written |
|---|---|---|
| Approval | all | all fields below |
| FundRelease | id, tier, amount, pools, purpose, amount_calc, status, event, risk_index | status via FR7 `transition_release` |
| RiskIndex | score, tier, run_date, peak_lead_day, explanation_en/ur, top_factors, is_fallback, is_stale_input | — |
| FloodForecast | district-level rows for the RiskIndex run_date, lead days 1–7 | — |
| SatelliteFloodMap | district tehsils, `image_date` ≥ event `opened_at` − 7 days | — |
| Beneficiary | count by type in the district | — |
| User | role, district_id, phone (to send), phone_hash, is_active | — |
| DisasterFund | allocated_pkr, remaining_pkr | — |

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Field | Type | Purpose |
|---|---|---|---|
| Approval | `scenario_id` | FK ReplayScenario, nullable | Shared rule 2: every operational row carries the scenario (missing today) |
| Approval | `channel` | make **nullable** until decided | The channel is unknown at request time |
| Approval | `otp_hash`, `otp_expires_at` | make **nullable** | OTP is issued on demand, not at request time |
| Approval | `otp_attempts` | int, default 0 | The "max 5 attempts" rule needs a counter |
| Approval | `otp_last_sent_at` | timestamptz, nullable | 60 s resend cooldown |
| Approval | `wa_code_hash` | str, nullable | WhatsApp `APPROVE <code>` (D3) |
| Approval | `wa_code_expires_at` | timestamptz, nullable | 24 h code lifetime |
| Approval | `wa_code_attempts` | int, default 0 | 5-attempt lock |
| Approval | `escalation_reason` | enum `NO_RESPONSE_2H`, `NO_DC_ASSIGNED`, nullable | Why it escalated |
| Approval | `decision_message_id` | str, nullable, unique | Provider message id of the deciding WhatsApp reply (idempotency) |
| Alert | `purpose` + `DECISION_NOTICE` | enum value | Officer notices on reject/decided-after-escalation (fallback: `ESCALATION`) |

## API contracts

All under `/api/v1`, tag **Triggers**.

### GET `/approvals?decision=PENDING&escalated=true&district_id=&page=1&page_size=20`
Roles: DC (own district only), PDMA_OFFICER, ADMIN. DONOR → 403.
```json
{
  "items": [{
    "id": "ap1…", "decision": "ESCALATED", "district": {"id": "5d1c…", "name": "Dadu"},
    "fund_release_id": "r3…", "tier": "ACTION", "amount_pkr": "30000000.00",
    "requested_at": "2026-11-12T03:00:00Z", "deadline_at": "2026-11-12T05:00:00Z",
    "escalated_at": "2026-11-12T05:01:00Z", "escalation_reason": "NO_RESPONSE_2H",
    "simulated": false, "scenario_id": null
  }],
  "total": 1, "page": 1, "page_size": 20
}
```

### GET `/approvals/{approval_id}`
Roles: DC (own district), PDMA_OFFICER, ADMIN.
```json
{
  "id": "ap1…", "decision": "PENDING", "channel": null,
  "requested_at": "2026-11-12T03:00:00Z", "deadline_at": "2026-11-12T05:00:00Z",
  "escalated_at": null, "escalation_reason": null,
  "decided_at": null, "approver": null, "reason": null,
  "district": {"id": "5d1c…", "name": "Dadu", "flood_mode": "SLOW_ONSET"},
  "release": {
    "id": "r3…", "tier": "ACTION", "status": "PENDING_APPROVAL",
    "amount_pkr": "30000000.00", "household_pool_pkr": "21000000.00", "shopkeeper_pool_pkr": "9000000.00",
    "purpose": "Cash support",
    "amount_calc": {"cumulative_pct": 100, "target_pkr": "50000000.00", "prior_released_pkr": "20000000.00", "capped": false}
  },
  "event": {"id": "e1…", "opened_at": "2026-11-05T02:10:00Z", "released_so_far_pkr": "20000000.00",
            "releases": [{"tier": "WATCH", "amount_pkr": "5000000.00", "status": "RELEASED"},
                         {"tier": "WARNING", "amount_pkr": "15000000.00", "status": "RELEASED"}]},
  "risk": {"risk_index_id": "ri…", "run_date": "2026-11-12", "score": 86, "tier": "ACTION", "peak_lead_day": 2,
           "explanation_en": "Very heavy rain expected in 2 days; soil already saturated…",
           "explanation_ur": "[Urdu text]",
           "top_factors": [{"factor": "rain_3d_mm", "value": 142, "contribution": 0.38}],
           "is_fallback": false, "is_stale_input": false},
  "latest_risk": {"run_date": "2026-11-12", "score": 86, "tier": "ACTION"},
  "forecast_7d": [{"lead_day": 1, "target_date": "2026-11-13", "probability": 0.71, "score": 71},
                  {"lead_day": 2, "target_date": "2026-11-14", "probability": 0.86, "score": 86}],
  "satellite": [{"tehsil_id": "t…", "tehsil_name": "Johi", "image_date": "2026-11-10",
                 "flooded_area_km2": 112.4, "flooded_pct": 31.2,
                 "before_tile_url": "/static/replay/sindh-2022/johi_before.png",
                 "after_tile_url": "/static/replay/sindh-2022/johi_after.png", "precomputed": true}],
  "register_counts": {"HOUSEHOLD": 1240, "SHOPKEEPER": 310},
  "fund": {"allocated_pkr": "50000000.00", "remaining_pkr": "49100000.00"},
  "otp": {"requested": false, "expires_at": null, "attempts_left": 5},
  "can_decide": true,
  "simulated": false, "scenario_id": null
}
```
`can_decide` is true only for the DC of the district while the decision is PENDING or ESCALATED. Errors: 404 `APPROVAL_NOT_FOUND`, 403 `DISTRICT_SCOPE_FORBIDDEN`.

### POST `/approvals/{approval_id}/otp`
Role: DC (own district). Empty body.

Response 202:
```json
{"otp_expires_at": "2026-11-12T03:14:00Z", "sent_to": "+92******4567", "attempts_left": 5}
```
In non-production with `APPROVAL_OTP_DEV_ECHO=true`, the response also has `"dev_otp": "482913"`.

Errors: 409 `APPROVAL_ALREADY_DECIDED`, 409 `DC_PHONE_MISSING`, 429 `OTP_RESEND_TOO_SOON` (`retry_after_s`), 403 `APPROVER_NOT_DC`.

### POST `/approvals/{approval_id}/approve`
Role: DC (own district).
```json
{"otp": "482913"}
```
Response 200:
```json
{
  "approval": {"id": "ap1…", "decision": "APPROVED", "channel": "DASHBOARD", "approver_id": "u-dc-dadu",
               "decided_at": "2026-11-12T03:09:12Z", "simulated": false},
  "release": {"id": "r3…", "status": "RELEASED", "allocation_counts": {"FUNDED": 1480, "UNMET_NEED": 70},
              "pipeline_error": null}
}
```
Errors:
| HTTP | code | When |
|---|---|---|
| 400 | `OTP_INVALID` | wrong code; body has `attempts_left` |
| 409 | `OTP_NOT_REQUESTED` | no OTP issued |
| 410 | `OTP_EXPIRED` | past `otp_expires_at` |
| 429 | `OTP_LOCKED` | 5 failed attempts; request a new OTP |
| 409 | `APPROVAL_ALREADY_DECIDED` | APPROVED or REJECTED |
| 403 | `APPROVER_NOT_DC` | caller is not a DC of the district |

### POST `/approvals/{approval_id}/reject`
Role: DC (own district).
```json
{"reason": "River levels falling, camps already open"}
```
Response 200: same shape as approve, with `"decision": "REJECTED"`, `"reason": "…"`, release `"status": "REJECTED"`.
Errors: 422 `REJECT_REASON_REQUIRED` (< 5 or > 1000 chars), 409 `APPROVAL_ALREADY_DECIDED`, 403 `APPROVER_NOT_DC`.

### POST `/approvals/{approval_id}/resend-request`
Roles: DC (own district), PDMA_OFFICER. Issues a new WhatsApp code (old one invalid, attempts reset) and re-sends the request. Cooldown 60 s.

Response 202: `{"wa_code_expires_at": "2026-11-13T03:20:00Z", "sent_to": "+92******4567"}`. Errors: 409 `APPROVAL_ALREADY_DECIDED`, 409 `DC_PHONE_MISSING`, 429 `RESEND_TOO_SOON`.

### POST `/approvals/{approval_id}/simulate`
Role: ADMIN. Replay scenarios only.
```json
{"decision": "APPROVED", "reason": null}
```
Response 200: decision result with `"channel": "REPLAY_SIM", "simulated": true`. Errors: 403 `SIMULATION_LIVE_FORBIDDEN` (live approval), 409 `APPROVAL_ALREADY_DECIDED`.

## WhatsApp handler (called by FR15)

Module `app/services/triggers/dc_commands.py`:

```python
class DcVerb(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"

@dataclass(frozen=True)
class DcCommand:
    verb: DcVerb
    code: str | None          # 6 ASCII digits after normalisation
    reason: str | None        # REJECT only

class DcCommandOutcome(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CODE_INVALID = "CODE_INVALID"
    CODE_LOCKED = "CODE_LOCKED"
    CODE_EXPIRED = "CODE_EXPIRED"
    REASON_REQUIRED = "REASON_REQUIRED"
    AMBIGUOUS = "AMBIGUOUS"                 # several open approvals, no code
    NO_OPEN_APPROVAL = "NO_OPEN_APPROVAL"
    ALREADY_DECIDED = "ALREADY_DECIDED"     # includes duplicate delivery
    NOT_A_DC = "NOT_A_DC"

@dataclass(frozen=True)
class DcCommandResult:
    outcome: DcCommandOutcome
    approval_id: UUID | None
    scenario_id: UUID | None                # scenario of the approval, so FR15 sends its reply in the right scope
    reply_text: str                         # Urdu first, then English; FR15 sends it via template "dc.passthrough"
    state_changed: bool

def parse_dc_command(body: str) -> DcCommand | None: ...

def handle_dc_whatsapp_command(
    db: Session,
    *,
    sender_phone_hash: str,
    body: str,
    provider_message_id: str,
    received_at: datetime,
) -> DcCommandResult: ...
```

Contract with FR15:
- FR15 calls it only after the Twilio signature check and its own `provider_message_id` dedup, inside its request transaction, and commits.
- FR15 calls it when the sender `phone_hash` matches an active `User(role=DC)` **and** `parse_dc_command(body)` is not `None`. Otherwise the message is a beneficiary reply.
- FR15 sends `reply_text` back to the sender (TwiML `<Message>` in the webhook response).
- The handler never raises for user errors; it returns an outcome. It raises only on infrastructure errors (FR15 returns 500 so Twilio retries; idempotency covers the retry).

Grammar (after NFKC, digit normalisation `0-9` (Urdu digits)/`0-9` (Arabic-Indic digits) → `0-9`, whitespace collapse, case-fold):

| Input | Parsed |
|---|---|
| `APPROVE 482913`, `approve 482 913` | APPROVE, code 482913 |
| `APPROVE` | APPROVE, code None → `CODE_INVALID` reply with usage |
| `REJECT camps already open` | REJECT, reason |
| `REJECT 482913 camps already open` | REJECT, code, reason |
| anything else | `None` |

## Escalation job

`app/jobs/timers.py`:
```python
def job_escalate_overdue_approvals() -> None:          # every APPROVAL_ESCALATION_JOB_EVERY_MIN (5)
    with session_scope() as db, use_scenario(None):
        escalate_overdue_approvals(db, now=SystemClock().now(), scenario_id=None)
```
Registered via `register_job("escalate_overdue_approvals", job_escalate_overdue_approvals, minutes=5)`. FR5 calls the service directly per replay step with the replay clock.

## Audit entries

| event_type | entity | payload (never the code) |
|---|---|---|
| `APPROVAL_REQUESTED` | Approval | release_id, district_id, amount_pkr, score, deadline_at, dc_user_id or null, wa_code_expires_at |
| `APPROVAL_OTP_ISSUED` | Approval | user_id, expires_at, channel `WHATSAPP` |
| `APPROVAL_CODE_FAILED` | Approval | kind `OTP` or `WA_CODE`, attempt, attempts_left, locked |
| `APPROVAL_DECIDED` | Approval | release_id, decision, channel, approver_id, reason, simulated, was_escalated, minutes_to_decide, message_id |
| `APPROVAL_ESCALATED` | Approval | reason, requested_at, escalated_at, notified_officer_ids |
| `APPROVAL_REQUEST_RESENT` | Approval | by_user_id, wa_code_expires_at |

## Alerts enqueued (FR14 templates)

| template_key | recipient | purpose | sensitive_keys |
|---|---|---|---|
| `dc_approval_request` | DC | `APPROVAL_REQUEST` | `code` |
| `dc_approval_otp` | DC | `APPROVAL_REQUEST` | `otp` |
| `approval_reminder_dc` | DC | `ESCALATION` | — |
| `approval_escalated_officer` | each PDMA officer | `ESCALATION` | — |
| `approval_rejected_officer` | each PDMA officer | `DECISION_NOTICE` (fallback `ESCALATION`) | — |
| `approval_decided_officer` | each PDMA officer (only if escalated) | `DECISION_NOTICE` (fallback `ESCALATION`) | — |

All with `related={"approval_id": …, "fund_release_id": …}`.

## Configuration

| Env / setting | Default | Notes |
|---|---|---|
| `APPROVAL_ESCALATE_AFTER_MIN` | 120 | D3/D6 |
| `APPROVAL_ESCALATION_JOB_EVERY_MIN` | 5 | Escalation lag ≤ 5 min |
| `APPROVAL_OTP_TTL_MIN` | 10 | data-model |
| `APPROVAL_OTP_MAX_ATTEMPTS` | 5 | data-model |
| `APPROVAL_OTP_RESEND_COOLDOWN_S` | 60 | also used by resend-request |
| `APPROVAL_WA_CODE_TTL_H` | 24 | |
| `APPROVAL_WA_CODE_MAX_ATTEMPTS` | 5 | |
| `APPROVAL_REJECT_REASON_MIN_CHARS` | 5 | |
| `APPROVAL_OTP_DEV_ECHO` | false | Forced false when `ENV=production` |
| `REPLAY_APPROVAL_MODE` | `SIMULATED` | or `LIVE_WHATSAPP` |
| `REPLAY_APPROVAL_DELAY_MIN` | 45 | Replay decision time offset |
| `DASHBOARD_BASE_URL` | `http://localhost:5173` | Link in the request message |

## Project Structure

### Documentation (this feature)

```text
specs/008-fr08-dc-action-approval/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)

```text
app/
├── api/v1/approvals.py                    # 7 routes
├── schemas/approvals.py                   # ApprovalListItem, ApprovalDetail, OtpIssued, ApproveIn, RejectIn, SimulateIn, DecisionResult
├── services/triggers/
│   ├── approval_codes.py                  # generate_code, hash_code, verify_code, normalise_digits
│   ├── approvals.py                       # request_approval, issue_dashboard_otp, approve_with_otp, reject, resend_request,
│   │                                      #   decide, simulate_replay_decision, simulate_pending_approvals, escalate_overdue_approvals
│   ├── approval_view.py                   # build_approval_detail (risk, forecast, satellite, counts)
│   └── dc_commands.py                     # parse_dc_command, handle_dc_whatsapp_command, reply texts
└── jobs/timers.py                         # + job_escalate_overdue_approvals
alembic/versions/0008_fr08_approval_fields.py
tests/
├── unit/triggers/test_approval_codes.py
├── unit/triggers/test_dc_command_parser.py
├── integration/triggers/test_approval_gate.py
├── integration/triggers/test_approval_dashboard.py
├── integration/triggers/test_approval_whatsapp.py
├── integration/triggers/test_approval_escalation.py
├── integration/triggers/test_approval_replay.py
└── contract/test_approvals_contract.py
```

**Structure Decision**: single project, constitution layout.

## Complexity Tracking

None.
