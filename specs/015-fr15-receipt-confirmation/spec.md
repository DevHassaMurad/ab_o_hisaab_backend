# Feature Specification: FR15 Record a beneficiary's confirmation reply

**Feature Branch**: `015-fr15-receipt-confirmation`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "FR15 Record a beneficiary's confirmation reply" (Area: Communication, Priority: MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Beneficiary confirms receipt with one digit (Priority: P1)

A household or shopkeeper gets the payment notice (FR14) and replies `1` because the money arrived. They may type it on an Urdu keyboard (`۱`) or an Arabic keyboard (`١`). The system matches the phone to their latest payment notice, marks the receipt Confirmed, writes an audit entry and thanks them in Urdu and English (UC10 steps 2–3).

**Why this priority**: "An approved payment is not money received." This is the only signal that money actually reached the person, and the donor report's "received" figure depends on it (FR19).

**Independent Test**: Create a sent `PAYMENT_NOTICE` alert for a beneficiary, post a signed inbound message `Body=۱` from their number, and check one `Confirmation` with status `CONFIRMED`, one `RECEIPT_CONFIRMED` audit entry and one queued `receipt.ack.confirmed` reply.

**Acceptance Scenarios**:

1. **Given** a payment notice sent to +923001234567 an hour ago, **When** that number replies `1`, **Then** a `CONFIRMED` confirmation is linked to that alert and payment, an audit entry is written, and a thank-you reply is queued.
2. **Given** the same notice, **When** the reply is `۱`, `١`, `１`, ` 1 `, `1.` or `1 shukriya`, **Then** it is treated exactly like `1`.
3. **Given** the same notice, **When** the reply is `2` or `۲`, **Then** a `NOT_RECEIVED` confirmation is recorded and the reply says the report is recorded and will be checked.
4. **Given** a beneficiary who replied `2`, **When** they later reply `1`, **Then** a second confirmation row is added and the effective status for the payment becomes `CONFIRMED`.
5. **Given** two payment notices to the same beneficiary, **When** they reply `1`, **Then** the confirmation attaches to the most recent one.

---

### User Story 2 - DC approves or rejects by WhatsApp (Priority: P1)

The DC replies to the approval request with `APPROVE 482913` or `REJECT water is falling`. The system recognises the DC's phone and passes the message to the approval handler (FR8, D3), then sends the DC a short reply with the outcome: approved, rejected, wrong code, expired code, nothing pending, already decided, or reason missing.

**Why this priority**: Document Q17: "the DC approves by replying on WhatsApp", live on stage. The Action tier cannot release money otherwise (Principle II).

**Independent Test**: With a stubbed FR8 handler, post `APPROVE 482913` from the Dadu DC's number and check the handler was called once with the DC user, the text and the `MessageSid`, and that the rendered result reply is queued to the DC.

**Acceptance Scenarios**:

1. **Given** an active DC user with phone +923331112222, **When** a message `approve ۴۸۲۹۱۳` arrives from that number, **Then** the FR8 handler gets `approve 482913` (digits normalised), the DC user and the message id.
2. **Given** the handler returns `APPROVED`, **When** the reply is rendered, **Then** the DC gets "منظوری درج ہو گئی …" then "Approval recorded …".
3. **Given** the handler returns `INVALID_CODE`, **When** the reply is rendered, **Then** the DC is told the code is wrong and how to send it again.
4. **Given** `APPROVE 482913` comes from a beneficiary's number, **When** it arrives, **Then** it is never passed to the approval handler; it is handled as an unparseable receipt reply.
5. **Given** `APPROVE 482913` comes from a PDMA officer's number, **When** it arrives, **Then** the handler is not called and the officer is told only the DC can approve (D6).
6. **Given** the DC sends "hello", **When** it arrives, **Then** the DC gets the command help text.
7. **Given** the approval handler fails with an error, **When** the message is processed, **Then** nothing is half-written, and the DC is told to use the dashboard.

---

### User Story 3 - Every message is processed once and only from Twilio (Priority: P1)

Twilio may deliver the same message more than once, and anyone on the internet can call the webhook. The system rejects calls without a valid Twilio signature and processes each message id once.

**Why this priority**: Duplicate confirmations would inflate donor numbers; a forged `APPROVE` would bypass Principle II.

**Independent Test**: Post the same signed message three times (including two at the same time) and check one confirmation, one audit entry and one reply. Post an unsigned message and check 403 with no writes.

**Acceptance Scenarios**:

1. **Given** a message already processed, **When** the same `MessageSid` arrives again, **Then** the response is 200 and nothing new is written or sent.
2. **Given** two deliveries of one `MessageSid` at the same time, **When** both are processed, **Then** exactly one confirmation exists.
3. **Given** a request with a missing or wrong signature, **When** it arrives, **Then** the response is 403 and nothing is written.

---

### User Story 4 - Unclear replies get help, strangers get silence (Priority: P2)

If a beneficiary sends something the system cannot read (a word, `12`, a voice note), the reply is recorded as unparseable and they get one short help message asking for `1` or `2`. A beneficiary with no open payment notice is told nothing is waiting. A number the system does not know gets no reply.

**Why this priority**: Low-literacy users (NFR10) need a gentle second chance; spam must not trigger outbound messages.

**Independent Test**: Post `ok`, a media-only message and `12` from a beneficiary with an open notice; check three `UNPARSEABLE` rows and one help reply (throttled). Post `1` from an unknown number; check no rows and no reply.

**Acceptance Scenarios**:

1. **Given** an open notice, **When** the reply is `ok`, **Then** an `UNPARSEABLE` confirmation is stored and a help reply is queued.
2. **Given** a help reply was sent to this phone in the last 10 minutes, **When** another unparseable reply arrives, **Then** it is recorded but no new help reply is sent.
3. **Given** a beneficiary with no notice in the last 7 days, **When** they reply `1`, **Then** no confirmation is stored and they get "no payment is waiting for your reply".
4. **Given** an unknown number, **When** it sends anything, **Then** nothing is stored and nothing is sent.
5. **Given** an open notice, **When** a voice note with no text arrives, **Then** an `UNPARSEABLE` confirmation with the note "[media]" is stored and a help reply is queued.

---

### User Story 5 - Officers and DCs see who confirmed (Priority: P2)

Officers and the district's DC can list confirmations and see a summary for a release or district: notified, confirmed, not received, unclear only, and no reply. Payments and the donor report use the same numbers.

**Why this priority**: The DC follows up "not received" cases (UC09); donors see "received" (UC11-2).

**Independent Test**: Contract tests on `GET /confirmations` and `GET /confirmations/summary`, plus a service test of `receipt_counts` against a fixture.

**Acceptance Scenarios**:

1. **Given** 10 paid and notified payments with 6 confirmed, 1 not received, 1 unparseable only and 2 silent, **When** the summary is requested, **Then** it shows 10 / 6 / 1 / 1 / 2 and a confirmation rate of 60%.
2. **Given** a DC of Buner, **When** they list confirmations for Dadu, **Then** they get 403.
3. **Given** a donor, **When** they call either endpoint, **Then** they get 403 (donors use the aggregated FR19 report).

---

### User Story 6 - Simulated replies make replays realistic (Priority: P3)

During a replay, thousands of payment notices go to synthetic beneficiaries through the dry-run sender. A small job generates realistic, clearly labelled replies for them (most confirm, a few say not received, some stay silent), so the receipt numbers and the donor report look like a real rollout. Real phones (the hero shopkeeper) are never simulated.

**Why this priority**: Demo quality only; honest because every simulated reply is labelled (Principle VIII).

**Independent Test**: Run the job over 1 000 dry-run notices with the default mix and a fixed seed; check about 850 confirmed, 50 not received, 100 silent, all `simulated=true`, and none for alerts that were really sent.

**Acceptance Scenarios**:

1. **Given** dry-run payment notices in a replay, **When** the job runs, **Then** each gets at most one simulated reply, chosen deterministically from its alert id.
2. **Given** a notice really sent to the hero phone, **When** the job runs, **Then** it is skipped.
3. **Given** the job runs twice, **When** it finishes, **Then** no extra confirmations exist.

---

### Edge Cases

- **Digits and marks**: ASCII `1`/`2`, Extended Arabic-Indic `۱`/`۲` (Urdu keyboards), Arabic-Indic `١`/`٢`, full-width `１`/`２`; leading/trailing spaces, RLM/LRM/zero-width/BOM marks, trailing punctuation (`.`, `۔`, `!`, `؟`, `،`).
- **Ambiguous**: `12`, `1 2`, `21`, `3`, `0`, empty body → unparseable.
- **Words**: exact-match keywords only (see Reply grammar); "mil gaya" counts, "kab milega?" does not.
- **Quick-reply buttons** (production): a button payload `RECEIPT_YES` / `RECEIPT_NO` is used before the body.
- **Non-Pakistani number** (e.g. a sandbox test phone abroad): treated as an unknown sender, because the foundation only hashes Pakistani mobiles. Demo phones must be Pakistani numbers.
- **Same phone for DC and beneficiary** (demo): commands go to FR8; digits go to the receipt flow.
- **Phone shared by two beneficiaries**: the latest open notice across both wins.
- **Reply after the 7-day window**: "no payment is waiting".
- **Reply to a tier alert only** (no payment yet): "no payment is waiting".
- **Notice was FAILED on WhatsApp but sent by SMS (FR16)**: the SMS alert is a valid open notice; a WhatsApp reply to it still counts.
- **Replay vs live**: the confirmation takes the scenario of the alert it answers; audit goes to that chain.
- **Outcome survey open (FR22 on)**: the reply goes to the survey handler, not to receipts.
- **Twilio retries or webhook timeouts**: processing finishes well under Twilio's timeout; the response is always fast.

## Reply grammar

| Input after normalisation | Result |
|---|---|
| First token is `1` and no other digit token | `CONFIRMED` |
| First token is `2` and no other digit token | `NOT_RECEIVED` |
| Whole message equals a "yes" keyword: `ہاں`, `جی`, `جی ہاں`, `مل گیا`, `مل گئے`, `مل گئی`, `haan`, `han`, `ji`, `yes`, `mil gaya`, `mil gaye`, `received` | `CONFIRMED` |
| Whole message equals a "no" keyword: `نہیں`, `نہیں ملا`, `نہیں ملے`, `nahi`, `nahin`, `nai`, `no`, `nahi mila`, `not received` | `NOT_RECEIVED` |
| Button payload `RECEIPT_YES` / `RECEIPT_NO` | `CONFIRMED` / `NOT_RECEIVED` |
| Anything else, including media-only | `UNPARSEABLE` |

Normalisation: Unicode NFKC; map `۰–۹` and `٠–٩` to `0–9`; remove U+200B–U+200F, U+202A–U+202E, U+2066–U+2069, U+FEFF; collapse whitespace; trim; strip trailing `.`, `۔`, `!`, `؟`, `?`, `،`, `,`; lower-case Latin.

A DC **command** is a message whose first word, case-insensitive, is `APPROVE` or `REJECT`.

## Reply message catalogue

All Urdu first, then English (NFR10). Purpose `REPLY_ACK`.

| Key | Urdu | English |
|---|---|---|
| `receipt.ack.confirmed` | شکریہ۔ آپ نے بتایا کہ رقم مل گئی ہے۔ یہ درج کر لیا گیا ہے۔ | Thank you. You said you got the money. This is recorded. |
| `receipt.ack.not_received` | افسوس ہے۔ آپ نے بتایا کہ رقم نہیں ملی۔ یہ درج کر لیا گیا ہے اور اس کی جانچ کی جائے گی۔ حوالہ: {ref} | Sorry. You said you did not get the money. This is recorded and will be checked. Ref: {ref} |
| `receipt.help` | براہ کرم صرف ایک نمبر بھیجیں: رقم مل گئی تو 1، نہیں ملی تو 2۔ | Please send just one number: 1 if you got the money, 2 if not. |
| `receipt.no_open_notice` | آپ کا پیغام مل گیا۔ اس وقت آپ کی کوئی ادائیگی جواب کی منتظر نہیں ہے۔ | We got your message. No payment is waiting for your reply right now. |

DC and official replies. **Reconciled 2026-11-10**: FR8 owns this wording and returns it as `DcCommandResult.reply_text`, which FR15 forwards unchanged through the `dc.passthrough` template. The texts below are the reference wording FR8 should use for each outcome (FR8 outcome names: `CODE_INVALID`, `CODE_EXPIRED`, `CODE_LOCKED`, `NO_OPEN_APPROVAL`, …):

| Key | Urdu | English |
|---|---|---|
| `dc.approval_result.APPROVED` | منظوری درج ہو گئی۔ ضلع {district_ur} کے لیے {amount} روپے کی ادائیگیاں شروع ہو رہی ہیں۔ | Approval recorded. Payments of Rs {amount} for {district} are starting. |
| `dc.approval_result.REJECTED` | انکار درج ہو گیا۔ وجہ: {reason}۔ رقم فنڈ میں رہے گی اور پی ڈی ایم اے کو اطلاع دے دی گئی ہے۔ | Rejection recorded. Reason: {reason}. The money stays in the fund and PDMA has been informed. |
| `dc.approval_result.INVALID_CODE` | کوڈ درست نہیں۔ باقی کوششیں: {attempts_left}۔ دوبارہ بھیجیں: | Wrong code. Attempts left: {attempts_left}. Send again: |
| | APPROVE اور 6 ہندسوں کا کوڈ | APPROVE and the 6-digit code |
| `dc.approval_result.CODE_EXPIRED` | کوڈ کی مدت ختم ہو چکی ہے۔ نیا کوڈ ڈیش بورڈ پر یا نئے پیغام میں ملے گا۔ | The code has expired. A new code is on the dashboard or in a new message. |
| `dc.approval_result.NO_OPEN_APPROVAL` | اس وقت آپ کے ضلع کی کوئی منظوری زیر التوا نہیں ہے۔ | No approval is waiting for your district right now. |
| `dc.approval_result.ALREADY_DECIDED` | اس درخواست کا فیصلہ پہلے ہی ہو چکا ہے۔ | This request has already been decided. |
| `dc.approval_result.REASON_REQUIRED` | رد کرنے کی وجہ بھی لکھیں، مثال کے طور پر: | Please add a reason, for example: |
| | REJECT پانی کم ہو رہا ہے | REJECT water is falling |
| `dc.approval_result.ERROR` | معذرت، ابھی فیصلہ درج نہیں ہو سکا۔ براہ کرم ڈیش بورڈ استعمال کریں: {link} | Sorry, we could not record this now. Please use the dashboard: {link} |
| `dc.help` | آب و حساب: منظوری کے لیے بھیجیں: APPROVE کوڈ — رد کرنے کے لیے بھیجیں: REJECT وجہ | Aab o Hisaab: to approve, send APPROVE <code>. To reject, send REJECT <reason>. |
| `official.not_authorised` | صرف متعلقہ ضلع کا ڈی سی منظوری دے سکتا ہے۔ | Only the district's DC can approve. |

(Two-row entries are one message with a line break; the command line stands alone so right-to-left layout does not reorder it.)

## Requirements *(mandatory)*

### Functional Requirements

- **FR15-001**: System MUST accept inbound WhatsApp messages only with a valid Twilio signature; others get 403 and cause no writes.
- **FR15-002**: System MUST process each inbound message id at most once, including concurrent duplicate deliveries.
- **FR15-003**: System MUST identify the sender by the hash of the normalised phone number, never by storing or comparing raw numbers in queries.
- **FR15-004**: System MUST route by this precedence: (1) a command from an active DC user → approval handler; (2) a command from another official → "only the DC can approve"; (3) a sender who is a beneficiary → receipt flow; (4) any other message from an official → command help; (5) unknown sender → no action, no reply.
- **FR15-005**: System MUST parse receipt replies per the Reply grammar, including Urdu and Arabic-Indic digits.
- **FR15-006**: System MUST attach a receipt reply to the sender's most recent reply-expecting payment notice sent within the reply window (default 7 days), across all beneficiaries sharing that phone.
- **FR15-007**: System MUST store every receipt reply (confirmed, not received or unparseable) as a new confirmation row with the alert, beneficiary, payment, channel, time, raw text and message id, and write one audit entry in the same transaction (UC10-3, Principle III). The audit payload holds no raw text.
- **FR15-008**: The effective receipt status of a payment MUST be its latest confirmed or not-received reply; unparseable replies never override it. Statuses: `CONFIRMED`, `NOT_RECEIVED`, `UNPARSEABLE_ONLY`, `NO_REPLY`, `NOT_NOTIFIED`.
- **FR15-009**: System MUST pass DC commands to the approval handler with the DC user, the text (digits normalised, outer whitespace trimmed) and the message id, in the same transaction as the reply it queues.
- **FR15-010**: System MUST send an Urdu-first acknowledgement for every processed receipt reply and DC command, using the reply catalogue; help replies are throttled to one per phone per 10 minutes.
- **FR15-011**: If the approval handler fails, the system MUST roll back its writes and still reply to the DC with the dashboard fallback.
- **FR15-012**: Confirmations MUST carry the scenario of the alert they answer, and audit entries MUST go to that scenario's chain.
- **FR15-013**: Confirmations MUST be marked simulated when generated by the reply simulator or when they answer a simulated payment (NFR13).
- **FR15-014**: Officers and admins MUST be able to list confirmations and get a receipt summary by release or district; DCs only for their district; donors never.
- **FR15-015**: System MUST provide receipt status and counts to the payment tracking (FR17) and donor report (FR19) features, with one shared definition.
- **FR15-016**: In replays, a job MUST generate deterministic simulated replies for dry-run payment notices only, using a configurable mix (default 85% confirmed, 5% not received, 10% silent).
- **FR15-017**: When the most recent open reply-expecting alert is an outcome survey and the FR22 flag is on, the reply MUST go to the survey handler instead.
- **FR15-018**: The webhook MUST answer within 2 seconds at p95 and always with an empty TwiML body; replies go through the outbound queue.

### Key Entities

- **Confirmation** (data-model §7): one inbound reply about one alert; status `CONFIRMED`, `NOT_RECEIVED` or `UNPARSEABLE`; channel; raw reply; received time. FR15 proposes message id, scenario, simulated and recorded-by fields, and append-only cardinality (plan.md).
- **Alert** (§7): the payment notice being answered (`expects_reply`, `to_phone_hash`), and the acknowledgement sent back (`REPLY_ACK`).
- **Beneficiary** (§5): matched by `phone_hash`.
- **User** (§8): DC / officer matched by `phone_hash`.
- **Payment** (§6): linked through the alert; receipt status is reported per payment.
- **Approval** (§4): changed only by FR8's handler.
- **AuditLog** (§8): one entry per confirmation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (NFR10): 100% of the parser corpus (≥ 40 cases across ASCII, Urdu, Arabic-Indic and full-width digits, marks, punctuation, keywords and ambiguous inputs) gives the expected result.
- **SC-002**: 3 deliveries (2 concurrent) of one message id produce exactly 1 confirmation, 1 audit entry and 1 acknowledgement.
- **SC-003**: Webhook p95 < 2 s and p99 < 5 s under 20 concurrent messages (well below Twilio's webhook timeout, **verify** 15 s).
- **SC-004** (NFR7): 100% of confirmations have exactly one audit entry; 0 raw replies in audit payloads; the chain verifies.
- **SC-005** (Principle II): 100% of commands from active DC phones reach the approval handler; 0 commands from any other phone do.
- **SC-006**: In the demo, the dashboard shows the hero's receipt as Confirmed within 5 s of the reply reaching Twilio.
- **SC-007**: 100% of unsigned or wrongly signed requests are rejected with 403 and 0 writes.
- **SC-008** (UC11-2): `receipt_counts` equals a hand-computed fixture exactly; FR19 and the summary endpoint return identical numbers for the same filter.

## Assumptions

- Payment notices are sent by FR14 with `expects_reply=true` and `to_phone_hash` set.
- FR8 provides `handle_dc_whatsapp_command(db, *, sender_phone_hash, body, provider_message_id, received_at) -> DcCommandResult` in `app/services/triggers/dc_commands.py`, is idempotent per message id, and does not send its own reply to the DC.
- Twilio inbound webhooks post form fields including `MessageSid`, `From`, `To`, `Body`, `NumMedia`, and for quick replies `ButtonPayload` (**verify** names).
- Twilio does not automatically retry failed inbound webhooks by default but may still deliver duplicates (**verify**); FR15 is safe either way.
- A beneficiary's reply refers to their latest notice; most beneficiaries have one open notice at a time.
- Raw replies may contain personal details, so they are treated as PII.

## Traceability

| Source | Covered by |
|---|---|
| UC10 step 2 (beneficiary replies 1 to confirm) | US1, FR15-005, FR15-006 |
| UC10 step 3 (mark receipt Confirmed and write an audit entry) | US1, FR15-007 |
| UC10 postcondition (receipt Confirmed, or a fallback starts) | FR15-008 (FR16 reads `NO_REPLY`) |
| UC07 trigger and step 3, as resolved by D3 (WhatsApp `APPROVE <code>` / `REJECT <reason>`) | US2, FR15-004, FR15-009 |
| Document Q17 (DC approves by replying on WhatsApp) | US2 |
| UC11 step 2 ("received") and UC09 | US5, FR15-015 |
| NFR6 / NFR8 | FR15-003, FR15-014, encrypted raw replies |
| NFR7 | FR15-007, SC-004 |
| NFR10 | Reply grammar, reply catalogue |
| NFR12 / Principle II | FR15-004, SC-005 |
| NFR13 | FR15-013, US6 |
| D3, D6 | US2 scenarios 1, 5 |
