# Agent Brief: FR8 DC Approval of Action-Tier Releases

## Owner role
Backend & alerts

## Mission
Make sure the largest release (Action, 60%) never moves without a recorded Deputy Commissioner decision (Principle II, NFR12). Build one `Approval` record that two channels can decide (D3): the dashboard (Approve plus a 6-digit OTP) and WhatsApp (`APPROVE <code>` / `REJECT <reason>`). Escalate to the PDMA officer after 2 hours without a decision (D6). On approval, start allocation and payment through FR7's pipeline. On rejection, log the reason, keep the money in the fund and notify the PDMA officer.

## Scope
**In (MVP)**
- `request_approval(db, release)` called by FR7 when Action fires.
- Approval detail view (UC07-2): score, explanation, 7-day forecast, satellite map if any, total amount and pools.
- Dashboard OTP: request, verify (10 min, 5 attempts), approve; reject with reason.
- WhatsApp approval code and the handler `handle_dc_whatsapp_command(...)` that FR15 calls.
- Escalation job (2 h) notifying PDMA officers; the DC can still decide afterwards.
- Replay: simulated decision (`channel=REPLAY_SIM`, `simulated=true`) or a real WhatsApp approval during the live demo.
- Audit for request, OTP issue/failure, decision and escalation.

**Out**
- Inbound webhook, Twilio signature check, dedup by `provider_message_id` (FR15 `015-fr15-receipt-confirmation`).
- Message rendering and sending (FR14 `014-fr14-whatsapp-alerts`).
- Allocation and payments (FR11/12, FR17), reached through FR7's `run_release_pipeline`.
- The PDMA Director General role and DG approval (roadmap, D6).
- Approving Watch/Warning (they are automatic, FR7).

## Read first
- `specs/_authoring-guide.md`
- `.specify/memory/constitution.md` (Principles II, III, VI, VIII; D3, D6)
- `specs/000-foundation/data-model.md` §4 (FundRelease, Approval), §3 (RiskIndex, FloodForecast), §2 (SatelliteFloodMap), §7 (Alert), §8 (User)
- `specs/000-foundation/plan.md` (security, interfaces, seed users)
- `specs/007-fr07-auto-tier-release/plan.md` (state machine, `run_release_pipeline`, `transition_release`)
- `specs/015-fr15-receipt-confirmation/plan.md` once written (routing to this handler)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | `User`, `require_role`, `Clock`, `ScenarioContext`, scheduler, bcrypt helpers |
| Depends on | FR20 | `AuditService.record` |
| Depends on | FR7 | `transition_release`, `run_release_pipeline`; FR7 calls `request_approval` |
| Depends on | FR14 | `AlertService.enqueue` for requests, OTPs and escalations; masking of sensitive context keys |
| Depends on | FR3, FR21 (read only) | RiskIndex, FloodForecast, SatelliteFloodMap for the detail view |
| Blocks | FR15 | Needs `parse_dc_command` and `handle_dc_whatsapp_command` |
| Blocks | FR5 | Replay needs `simulate_pending_approvals(..., scenario_id)` and `escalate_overdue_approvals(..., scenario_id)` |
| Blocks | FR11/12, FR17 for Action | Nothing is allocated for Action until approval |

## Interfaces
**Consumes**: `AuditService.record`, `AlertService.enqueue`, FR7 `transition_release`, FR7 `run_release_pipeline`, `Clock`, `use_scenario(scenario_id)`, `app/core/security.hash_secret/verify_secret` (bcrypt).

**Provides** (`app/services/triggers/approvals.py`, `app/services/triggers/dc_commands.py`)
- `request_approval(db, release: FundRelease) -> Approval`
- `issue_dashboard_otp(db, approval_id: UUID, user: User) -> OtpIssued`
- `approve_with_otp(db, approval_id: UUID, user: User, otp: str) -> DecisionResult`
- `reject(db, approval_id: UUID, user: User, reason: str) -> DecisionResult`
- `resend_request(db, approval_id: UUID, user: User) -> Approval`
- `decide(db, approval, *, decision, channel, approver, reason=None, simulated=False, message_id=None, decided_at=None) -> DecisionResult`
- `simulate_replay_decision(db, approval, *, decision="APPROVED", reason=None) -> DecisionResult`
- `simulate_pending_approvals(db, *, scenario_id: UUID) -> list[DecisionResult]`
- `escalate_overdue_approvals(db, *, now: datetime, scenario_id: UUID | None) -> list[Approval]`
- `parse_dc_command(body: str) -> DcCommand | None`
- `handle_dc_whatsapp_command(db, *, sender_phone_hash: str, body: str, provider_message_id: str, received_at: datetime) -> DcCommandResult`
- Job `job_escalate_overdue_approvals()` in `app/jobs/timers.py`

## Files you own
- `app/services/triggers/approvals.py`
- `app/services/triggers/approval_codes.py`
- `app/services/triggers/dc_commands.py`
- `app/services/triggers/approval_view.py`
- `app/api/v1/approvals.py`
- `app/schemas/approvals.py`
- `app/jobs/timers.py` (only the `job_escalate_overdue_approvals` function and its registration; FR16/FR17 add their own jobs)
- `alembic/versions/0008_fr08_approval_fields.py`
- `tests/unit/triggers/test_approval_codes.py`
- `tests/unit/triggers/test_dc_command_parser.py`
- `tests/integration/triggers/test_approval_dashboard.py`
- `tests/integration/triggers/test_approval_whatsapp.py`
- `tests/integration/triggers/test_approval_escalation.py`
- `tests/integration/triggers/test_approval_replay.py`
- `tests/integration/triggers/test_approval_gate.py`
- `tests/contract/test_approvals_contract.py`

## Guardrails
| Principle | How it applies |
|---|---|
| II Human authority | Only a `DC` user of the release's district can approve. PDMA officers, admins and donors get 403. `simulate_replay_decision` refuses live approvals. No endpoint approves without an OTP or a verified WhatsApp code. |
| III Traceability | `APPROVAL_REQUESTED`, `APPROVAL_OTP_ISSUED`, `APPROVAL_CODE_FAILED`, `APPROVAL_DECIDED`, `APPROVAL_ESCALATED` audited in the same transaction. Codes never appear in payloads. |
| VI Privacy | DC sees only their district. Phone numbers are masked in responses. OTPs and codes are stored as bcrypt hashes only. |
| VIII Honest | Replay decisions are `channel=REPLAY_SIM`, `simulated=true`. |
| X Simplicity | Escalation is an APScheduler interval job; no workflow engine. |
| XI Tests | Approval gating is a mandatory test-first money path. |

## Definition of Done
- [ ] All `tasks.md` items done, tests first.
- [ ] Gate tests: officer/admin/other-district DC cannot approve; wrong, expired or locked OTP never approves; no Allocation exists before approval.
- [ ] WhatsApp: `APPROVE <code>` approves; `REJECT <reason>` rejects; Urdu digits and keywords parse; ambiguity asks for the code; duplicate messages are no-ops.
- [ ] Escalation flips to `ESCALATED` between 2 h and 2 h 5 min, notifies officers, and the DC can still decide.
- [ ] Rejection keeps the money in the fund (`available_to_release` restored) and notifies officers.
- [ ] Replay simulated approvals are labelled and still audited.
- [ ] `ruff`, `mypy --strict app/services/triggers` clean.

## Hand-off notes
- **FR15**: for an inbound WhatsApp message whose sender `phone_hash` belongs to an active `DC` user and where `parse_dc_command(body)` is not `None`, call `handle_dc_whatsapp_command(...)` inside your transaction and return `result.reply_text` to the sender (TwiML reply or `AlertService`). Dedup by `provider_message_id` before calling; the handler is idempotent too.
- **FR14**: needs templates `dc_approval_request`, `dc_approval_otp`, `approval_reminder_dc`, `approval_escalated_officer`, `approval_rejected_officer`, `approval_decided_officer`. Context keys listed in `sensitive_keys` (`code`, `otp`) must be rendered into the outgoing message but masked (`******`) in the stored `Alert.message_text`.
- **FR5**: at each replay step, after `TriggerEngine.evaluate`, call `simulate_pending_approvals(db, scenario_id=sid)` (no-op unless `REPLAY_APPROVAL_MODE=SIMULATED`), then `escalate_overdue_approvals(db, now=replay_clock.now(), scenario_id=sid)`.
- **FR19/FR20**: the `Approval` row and its audit entries are the proof shown to donors that a human approved the large release (aggregate only).
