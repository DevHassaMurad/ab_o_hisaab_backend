# Agent Brief: FR15 Record a beneficiary's confirmation reply

## Owner role
Backend & alerts.

## Mission
Own the inbound WhatsApp webhook. Check that each message really comes from Twilio, then decide what it is: a beneficiary's `1` (received) or `2` (not received) to a payment notice, or a DC's `APPROVE <code>` / `REJECT <reason>` (D3). Record receipts as `Confirmation` rows with an audit entry, route DC commands to the FR8 handler, and reply with a short Urdu-first acknowledgement. Every message is processed exactly once, even when Twilio delivers it twice.

## Scope
**In (MVP)**
- `POST /api/v1/webhooks/whatsapp`: Twilio signature check (shared dependency from FR14), always-fast TwiML response
- Reply parser: `1`/`2` in ASCII, Urdu (Extended Arabic-Indic `۱`/`۲`), Arabic-Indic (`١`/`٢`) and full-width digits; zero-width and bidi marks stripped; a short exact-match keyword list; media-only messages
- Sender matching by `phone_hash`: DC/official users first for commands, then beneficiaries; the latest open reply-expecting alert (`PAYMENT_NOTICE`) within the reply window
- `Confirmation` rows (append-only, latest valid reply wins) and audit entries `RECEIPT_CONFIRMED`, `RECEIPT_NOT_RECEIVED`, `RECEIPT_UNPARSEABLE`
- Routing DC messages to `handle_dc_whatsapp_command(db, *, sender_phone_hash, body, provider_message_id, received_at)` in `app/services/triggers/dc_commands.py` (FR8) and rendering its `DcCommandResult` back to the DC
- Acknowledgement templates (`receipt.*`, `dc.approval_result.*`, `dc.help`, `official.not_authorised`), sent through FR14's `send_reply`
- Idempotency by inbound `MessageSid` (advisory lock + unique keys)
- Receipt status service for FR17 and FR19: `receipt_status_for_payments`, `receipt_counts`
- Read APIs: `GET /confirmations`, `GET /confirmations/summary`
- Simulated replies for dry-run payment notices in replays (labelled `simulated=true`) so the donor report's "received" figure is meaningful in the demo

**Out**
- Sending messages, status callbacks, templates engine: `014-fr14-whatsapp-alerts`
- Approval logic, OTP checks, escalation: `008-fr08-dc-action-approval`
- SMS replies and field-agent outcomes: `016-fr16-sms-field-fallback` (Roadmap; reuses this parser and `record_confirmation`)
- Outcome survey answers: `022-fr22-outcome-reports` (Roadmap; FR15 hands a reply to its handler when the open prompt is a survey and `FR22_OUTCOME` is on)
- Roadmap: WhatsApp quick-reply buttons (the parser already reads `ButtonPayload`), voice-note transcription

## Read first
- `.specify/memory/constitution.md` (Principles II, III, VI, VIII; D3; Quality Standards "Idempotency", "Language")
- `specs/_authoring-guide.md` (FR15 facts)
- `specs/000-foundation/data-model.md` §7 `Alert`, `Confirmation`; §8 `User`; §5 `Beneficiary`
- `specs/000-foundation/implement.md` (`normalise_pk_phone`, `phone_hash`, `use_scenario`)
- `specs/014-fr14-whatsapp-alerts/plan.md` (outbox fields, `send_reply`, `require_twilio_signature`, webhooks router)
- `specs/008-fr08-dc-action-approval/plan.md` (`handle_dc_whatsapp_command`, `DcCommandResult`)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
| Direction | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | `phone_hash`, `get_db`, scenario context, `require_role` |
| Depends on | 020-fr20-audit-log | Confirmations are audited |
| Depends on | 014-fr14-whatsapp-alerts | Signature dependency, webhooks router, `send_reply`, `Alert.expects_reply`, `Alert.to_phone_hash` |
| Depends on | 008-fr08-dc-action-approval | `handle_dc_whatsapp_command`; until it lands, a stub returning `NO_OPEN_APPROVAL` keeps routing testable |
| Soft | 017-fr17-payment-tracking | Payment notices exist only after Paid; tests use factories |
| Blocks | 019-fr19-donor-report | "Received" = Paid with effective status `CONFIRMED` via `receipt_counts` |
| Blocks | 016-fr16-sms-field-fallback | Reuses `parse_reply`, `record_confirmation`, `effective_status` |
| Blocks | 005-fr05-event-replay | Live-demo DC approval over WhatsApp goes through this webhook |

## Interfaces
**Provides**
- `parse_reply(body: str, button_payload: str | None = None) -> ParsedReply` in `app/services/messaging/reply_parser.py`
- `route_inbound(db, msg: InboundMessage) -> InboundOutcome` in `app/services/messaging/inbound.py`
- `record_confirmation(db, *, alert, parsed, channel, provider_message_id, reply_raw, simulated, recorded_by=None) -> Confirmation` in `app/services/messaging/confirmations.py`
- `receipt_status_for_payments(db, payment_ids) -> dict[UUID, ReceiptStatus]` and `receipt_counts(db, *, district_id=None, fund_release_id=None, beneficiary_type=None) -> ReceiptCounts` in `app/services/messaging/confirmations.py`
- `simulate_replies(db, *, now) -> int` in `app/services/messaging/simulated_replies.py` (job)

**Consumes**
- `require_twilio_signature`, `send_reply` (FR14); `handle_dc_whatsapp_command` and `DcCommandResult` (FR8); `AuditService.record` (FR20); `phone_hash`, `normalise_pk_phone`, `use_scenario` (foundation); optional `handle_survey_reply(db, *, alert, beneficiary, parsed, provider_message_id)` (FR22)

## Files you own
- `app/services/messaging/reply_parser.py`, `inbound.py`, `confirmations.py`, `simulated_replies.py`
- The `receipt.*`, `dc.approval_result.*`, `dc.help`, `official.not_authorised` sections of `app/services/messaging/templates/catalogue.yaml`
- The "FR15" section of `app/api/v1/webhooks.py` (inbound route)
- `app/api/v1/confirmations.py`, `app/schemas/confirmations.py`
- `alembic/versions/<rev>_fr15_confirmation_fields.py`
- `tests/unit/messaging/test_reply_parser.py`, `test_command_detection.py`
- `tests/integration/messaging/test_inbound_receipt.py`, `test_inbound_dc_routing.py`, `test_inbound_idempotency.py`, `test_receipt_status.py`, `test_simulated_replies.py`
- `tests/contract/test_inbound_webhook.py`, `test_confirmations_api.py`
- `tests/fixtures/replies_corpus.yaml` (parser corpus)

## Guardrails
| Principle | How it applies here |
|---|---|
| II Human authority | Only an active `DC` user's phone can reach the FR8 handler. Officers and admins get "only the DC can approve". FR15 never decides an approval itself. |
| III Traceability | Each `Confirmation` row and its audit entry commit together. Payload: ids, status, channel, `reply_sha256`; never the raw reply. |
| VI Privacy | Raw reply text is encrypted at rest (proposed); API shows it only to officers and the district's DC. No donor endpoint here; FR19 gets counts only. |
| VII Degrade gracefully | An FR8 error still gets the DC a "use the dashboard" reply; Twilio always gets a 200 after a valid signature, so it does not retry into a loop. |
| VIII Honest prototype | Simulated replies are `simulated=true` with `SIMIN-` ids; replies about simulated payments are marked simulated too. |
| IX Config | Reply window, keyword list, help-reply throttle and simulated mix are settings. |
| XI Tests | Parser corpus, idempotency, routing precedence and signature tests first. |

## Definition of Done
- [ ] All tests above written first, seen failing, now green
- [ ] The parser corpus (≥ 40 cases, including `۱`, `١`, `１`, RLM-wrapped digits, `1.`, `ہاں`, `12`, `1 2`, empty, media-only) passes
- [ ] Three identical deliveries of one `MessageSid` produce one `Confirmation`, one audit entry and one ack (test)
- [ ] `APPROVE 123456` from the Dadu DC phone calls the FR8 handler once and the DC gets the rendered result; the same text from a beneficiary phone does not (tests)
- [ ] Bad signature → 403 and no writes (contract test)
- [ ] `receipt_counts` matches a hand-computed fixture for confirmed / not received / no reply (test)
- [ ] Live sandbox check: replying `1` and `۱` from the hero phone marks the receipt Confirmed within 5 s (manual)
- [ ] `ruff` and `mypy --strict app/services/messaging` clean

## Hand-off notes
- **008**: FR15 passes the text with digits normalised to ASCII and outer whitespace trimmed; keep `APPROVE`/`REJECT` parsing (case-insensitive) on your side. Return an `DcCommandResult` with at least `outcome` (one of `APPROVED`, `REJECTED`, `INVALID_CODE`, `CODE_EXPIRED`, `NO_OPEN_APPROVAL`, `ALREADY_DECIDED`, `REASON_REQUIRED`), `approval_id`, `scenario_id` and `context` (dict with `district`, `district_ur`, `amount`, `reason`, `attempts_left` as relevant). FR15 sends the reply; do not send it yourself. Be idempotent on `provider_message_id`.
- **019**: use `receipt_counts(...)`. "Received" = Paid payments whose effective status is `CONFIRMED`. Simulated confirmations are counted and labelled.
- **017**: use `receipt_status_for_payments` to show a receipt column on the payment list and timeline.
- **016**: call `record_confirmation(..., channel="SMS" | "FIELD_AGENT")`; the latest valid reply wins regardless of channel.
- **022**: implement `handle_survey_reply`; FR15 calls it only when the latest open reply-expecting alert is `OUTCOME_SURVEY` and the flag is on.
