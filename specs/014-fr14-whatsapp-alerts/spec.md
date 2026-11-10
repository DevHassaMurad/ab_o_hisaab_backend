# Feature Specification: FR14 WhatsApp alerts in Urdu and English

**Feature Branch**: `014-fr14-whatsapp-alerts`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "FR14 Send WhatsApp alerts in Urdu and English to the DC and beneficiaries" (Area: Communication, Priority: MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DC is told about a tier change and asked to approve (Priority: P1)

When a district's risk crosses a tier, the Deputy Commissioner gets a short WhatsApp message saying the new tier, the score, how much was released and for what. When the Action tier fires, the DC gets a second message with the amount and a one-time code, and can reply `APPROVE <code>` or `REJECT <reason>` (D3). This is the "phone buzzes on stage" moment of the demo.

**Why this priority**: The Action tier cannot release money without the DC (Principle II). If the DC does not hear about it fast, the anticipatory window is lost (NFR2, NFR12).

**Independent Test**: Create a fund release for Dadu at Action with a pending approval, call the tier-change and approval-request helpers, run the send worker with the dry-run sender, and check two alerts for the DC with status `SENT`, the right text, and `sent_at − triggered_at ≤ 60 s`.

**Acceptance Scenarios**:

1. **Given** Dadu's score rises to 62 and Watch and Warning fire in the same daily run, **When** the trigger engine reports the tier change, **Then** the Dadu DC gets one message (not two) that names both tiers, the score 62, the total released amount and the purposes, Urdu first then English.
2. **Given** Dadu reaches Action and the release is `PENDING_APPROVAL`, **When** the approval request is raised with code `482913`, **Then** the DC gets a message with the amount, the number of beneficiaries, the lines `APPROVE 482913` and `REJECT <reason>`, and the code's expiry time in Pakistan time.
3. **Given** the approval request was queued at 10:00:00 UTC, **When** the worker runs, **Then** the message is handed to the provider by 10:01:00 UTC and its status becomes `SENT`.
4. **Given** the DC user has no phone number on record, **When** an alert for the DC is enqueued, **Then** the alert is stored as `FAILED` with error `NO_PHONE`, an audit entry is written, and the approval still appears on the dashboard.
5. **Given** the score later falls from Warning to below Watch, **When** the trigger engine reports a downward change, **Then** the DC gets a message saying the risk fell and that released money is not taken back (D4).

---

### User Story 2 - Beneficiary is told money was sent and how to collect it (Priority: P1)

When a payment becomes Paid, the household or shopkeeper gets a WhatsApp message: how much, which wallet, how to cash it, that it is free, and "reply 1 if you got it, 2 if not" (UC08-5, UC10-1).

**Why this priority**: "An approved payment is not money received." This message starts the receipt confirmation loop (FR15) that the donor report depends on (FR19).

**Independent Test**: Mark a simulated payment Paid, call `notify_payment_paid`, run the worker, and check one `PAYMENT_NOTICE` alert with the amount, wallet, reply instructions and the demo label.

**Acceptance Scenarios**:

1. **Given** a simulated payment of PKR 15 000 to a JazzCash wallet becomes Paid, **When** the payment notice is enqueued, **Then** the text says "15,000 روپے" and "Rs 15,000", names JazzCash in both languages, tells the person to reply 1 or 2, and ends with the demo label.
2. **Given** the same payment notice was already enqueued, **When** the Paid hook runs again (a retry or re-run), **Then** no second alert is created.
3. **Given** a beneficiary whose phone is already flagged invalid, **When** a payment notice is enqueued, **Then** the alert is stored as `FAILED` with error `PHONE_INVALID` and no provider call is made.

---

### User Story 3 - Beneficiary gets a tier alert with safety advice (Priority: P1)

When a tier fires, every beneficiary in the district's allocation list for that release gets a short safety message for that tier, tailored to households or shopkeepers. If they are funded and no approval is pending, the message also says how much will be sent and to which wallet (UC10-1).

**Why this priority**: The point of anticipatory action is that people move themselves, their animals and their stock before the water arrives.

**Independent Test**: Fire Warning for a district with 3 funded households, 1 funded shopkeeper and 1 `UNMET_NEED` household; check 5 alerts with the right advice line and amount line only for the 4 funded ones.

**Acceptance Scenarios**:

1. **Given** a funded household at Warning, **When** tier alerts are sent, **Then** the message has the Warning header, the household advice line, the amount line and (for simulated data) the demo label.
2. **Given** a shopkeeper at Watch, **When** tier alerts are sent, **Then** the advice line is about moving shop stock.
3. **Given** the Action release is pending DC approval, **When** tier alerts are sent, **Then** the message has safety advice only and no amount.
4. **Given** a beneficiary marked `UNMET_NEED`, **When** tier alerts are sent, **Then** the message has safety advice only and no amount and no mention of being unfunded.
5. **Given** the score jumps straight to Action and Watch, Warning and Action all fire in one run, **When** tier alerts are sent, **Then** each beneficiary gets exactly one message, for the highest tier.

---

### User Story 4 - Delivery is tracked and bad numbers are flagged (Priority: P2)

The provider reports what happened to each message: sent, delivered, read, undelivered or failed. The system records the latest status, ignores out-of-order or repeated reports, and when the provider says a number is invalid it flags the beneficiary's record for update (UC10-1a).

**Why this priority**: Officers need to see who was not reached. A flagged record is how bad numbers get fixed.

**Independent Test**: Post signed status callbacks for an alert in the order `delivered`, `sent`, `read`, `read`; check the final status is `READ` and only two audit entries were written. Post `failed` with code 21211 for another alert; check the beneficiary's `phone_valid` is false.

**Acceptance Scenarios**:

1. **Given** an alert in `SENT`, **When** a callback says `delivered`, **Then** the status becomes `DELIVERED`, `delivered_at` is set, and one audit entry is written.
2. **Given** an alert in `DELIVERED`, **When** a late callback says `sent`, **Then** nothing changes.
3. **Given** a callback with a wrong or missing signature, **When** it arrives, **Then** the response is 403 and nothing changes.
4. **Given** a beneficiary alert, **When** a callback says `failed` with an invalid-number error, **Then** the alert becomes `FAILED` with that code and the beneficiary is flagged `phone_valid=false` with an audit entry.
5. **Given** a callback for a message id the system does not know, **When** it arrives, **Then** the response is 200 and nothing changes.

---

### User Story 5 - PDMA officer hears about escalations and decisions (Priority: P2)

If the DC does not answer an Action approval within 2 hours, the PDMA officer gets a WhatsApp notice (D6), and the DC gets a reminder. When the DC rejects, the officer is told the reason and that the money stays in the fund (UC07-3a).

**Why this priority**: D6 replaces the document's Director General escalation with the PDMA officer; the officer can chase the DC but cannot approve.

**Independent Test**: Call `notify_escalation` for a pending approval; check one `ESCALATION` alert for each active PDMA officer and one reminder for the DC. Call `notify_decision` for a rejected approval; check one `DECISION_NOTICE` for each officer with the reason.

**Acceptance Scenarios**:

1. **Given** an approval pending for 2 h, **When** the escalation is raised, **Then** each active PDMA officer gets a message saying the district, amount, hours waited, and that only the DC can approve.
2. **Given** the DC rejects with reason "water falling", **When** the decision notice is sent, **Then** each officer gets the reason and "the money stays in the fund".
3. **Given** the escalation was already sent for this approval, **When** the escalation timer runs again, **Then** no second escalation is sent.

---

### User Story 6 - Officers can see and test messaging (Priority: P3)

Officers and admins can list alerts with filters, see counts and latency for a release, preview every template, and (admin only) send a test message to a staff phone to check the sandbox. A failed alert can be re-sent after the number is fixed.

**Why this priority**: Useful for operations and for checking the sandbox before the demo; not on the money path.

**Independent Test**: Contract tests on the four read endpoints, the test-send endpoint and the resend endpoint, including role checks.

**Acceptance Scenarios**:

1. **Given** a DC of Dadu, **When** they list alerts, **Then** they only see alerts for Dadu.
2. **Given** a donor, **When** they call any alerts endpoint, **Then** they get 403.
3. **Given** a release with 200 alerts, **When** the officer asks for stats, **Then** they see counts by status and the p50, p95 and max of `sent_at − triggered_at`, plus the number of NFR2 breaches.
4. **Given** a failed alert and a corrected phone, **When** the officer re-sends it, **Then** a new alert is queued and linked to the old one, and the old one is unchanged.

---

### Edge Cases

- **Several tiers in one run** (D4): one DC message and one message per beneficiary, for the highest tier, with the total amount released so far in this run.
- **Score falls**: the DC gets a "risk fell" message; beneficiaries get nothing (no clawback, no confusing "all clear").
- **Event closes after 14 days below Watch**: the DC gets one "event closed" message.
- **No phone / phone invalid / not a Pakistani mobile**: the alert is stored `FAILED` (`NO_PHONE`, `PHONE_INVALID`) without calling the provider, so the gap is visible.
- **More than one active DC for a district**: each gets the message.
- **No active PDMA officer with a phone**: escalation alerts are stored `FAILED` (`NO_RECIPIENT`); the dashboard flag (FR8) still shows it.
- **Recipient has not joined the sandbox** (demo): the provider rejects it; the alert is `FAILED` with a "configuration" error class; the beneficiary is **not** flagged invalid.
- **Outside WhatsApp's 24-hour window** with no approved template: the provider rejects it; `FAILED` with a "template required" error class; admin is warned in the stats.
- **Provider outage or rate limit**: retried with backoff for up to 4 minutes, then `FAILED` (`PROVIDER_UNAVAILABLE`), so the NFR2 breach is visible rather than silent.
- **Process crash after the provider accepted a message but before it was recorded**: the message may be sent twice (at-least-once). This is accepted and documented.
- **Replay**: replay alerts carry the scenario id. Synthetic beneficiaries never get a real message; only allowlisted demo phones do.
- **Two beneficiaries share one phone**: each gets their own alert; replies are matched by FR15.
- **The same team phone is both the DC and the hero shopkeeper in the demo**: both messages are sent; FR15 routes replies by content.
- **District has no Urdu name on record**: the English name is used inside the Urdu text.
- **Outcome survey requested while FR22 is off**: refused with `FEATURE_DISABLED`.

## Message content catalogue

All messages are Urdu first, a blank line, then English. Beneficiary messages use short sentences, everyday words and no technical terms. Numbers use Western digits with thousands separators (`15,000`), because that is how amounts appear on wallet SMS in Pakistan. Tier names are never shown to beneficiaries as numbers, so they are never confused with the reply digits 1 and 2. `{variables}` are filled at enqueue time. Text in this section is the source for `catalogue.yaml`.

### Shared lines

| Key | Urdu | English |
|---|---|---|
| `line.demo` | (آزمائشی پیغام، یہ اصل رقم نہیں ہے) | (DEMO message, this is not real money) |
| `wallet.JAZZCASH` | جاز کیش | JazzCash |
| `wallet.EASYPAISA` | ایزی پیسہ | Easypaisa |
| `tier.WATCH` | نگرانی | Watch |
| `tier.WARNING` | خبردار | Warning |
| `tier.ACTION` | فوری خطرہ | Action |
| `purpose.WATCH` | کشتیاں اور خوراک پہلے سے پہنچانا | Preposition boats and food |
| `purpose.WARNING` | لوگوں کا انخلا اور کیمپ کھولنا | Evacuate, open camps |
| `purpose.ACTION` | نقد امداد | Cash support |

`line.demo` is appended to every beneficiary message when the beneficiary, release or payment is simulated (NFR13).

### TIER_ALERT to beneficiaries (`tier_alert.{TIER}.{TYPE}`)

Composed as: header + advice for the beneficiary type + amount line (only when funded and not pending approval) + `line.demo` (when simulated).

**Headers**

| Tier | Urdu | English |
|---|---|---|
| WATCH | آب و حساب: اگلے {lead_days} دن میں {district_ur} میں سیلاب کا خطرہ ہے۔ (نگرانی) | Aab o Hisaab: Flood risk in {district} in the next {lead_days} days. (Watch) |
| WARNING | آب و حساب: {district_ur} میں سیلاب کا بڑا خطرہ ہے۔ (خبردار) | Aab o Hisaab: High flood risk in {district}. (Warning) |
| ACTION | آب و حساب: {district_ur} میں سیلاب آنے والا ہے۔ (فوری خطرہ) | Aab o Hisaab: A flood is coming to {district}. (Action) |

**Advice lines**

| Tier | Type | Urdu | English |
|---|---|---|---|
| WATCH | HOUSEHOLD | شناختی کارڈ، دوائی، پانی اور خشک کھانا ایک تھیلے میں تیار رکھیں۔ | Keep ID cards, medicine, water and dry food ready in one bag. |
| WATCH | SHOPKEEPER | دکان کا قیمتی سامان اونچی جگہ رکھنے کا انتظام کریں۔ | Plan to move valuable shop stock to a high place. |
| WARNING | HOUSEHOLD | بچوں، بزرگوں اور مویشیوں کو اونچی جگہ لے جانے کی تیاری کریں۔ سرکاری اعلانات سنیں۔ | Get ready to move children, elders and animals to high ground. Listen to official announcements. |
| WARNING | SHOPKEEPER | دکان کا سامان اونچی یا محفوظ جگہ پر منتقل کریں۔ کاغذات اپنے ساتھ رکھیں۔ | Move shop stock to a high or safe place. Keep your papers with you. |
| ACTION | HOUSEHOLD | ابھی اونچی جگہ یا سرکاری کیمپ میں چلے جائیں۔ بہتے پانی میں نہ چلیں۔ | Go to high ground or a government camp now. Do not walk in flowing water. |
| ACTION | SHOPKEEPER | دکان بند کریں اور اونچی جگہ چلے جائیں۔ آپ کی جان سامان سے زیادہ قیمتی ہے۔ | Close the shop and go to high ground. Your life matters more than stock. |

**Amount lines**

| Key | When | Urdu | English |
|---|---|---|---|
| `amount.wallet` | Funded, has a wallet, release not pending approval | آپ کو {amount} روپے آپ کے {wallet_ur} اکاؤنٹ میں بھیجے جائیں گے۔ | You will get Rs {amount} in your {wallet_en} account. |
| `amount.no_wallet` | Funded, `wallet_provider = NONE` | آپ کے لیے {amount} روپے منظور ہوئے ہیں۔ رقم لینے کے لیے ڈی سی دفتر سے رابطہ کریں۔ | Rs {amount} has been approved for you. Contact the DC office to collect it. |
| (none) | `UNMET_NEED`, or Action pending approval | — | — |

**Full example** (`tier_alert.WARNING.HOUSEHOLD`, funded, simulated):

```text
آب و حساب: دادو میں سیلاب کا بڑا خطرہ ہے۔ (خبردار)
بچوں، بزرگوں اور مویشیوں کو اونچی جگہ لے جانے کی تیاری کریں۔ سرکاری اعلانات سنیں۔
آپ کو 7,000 روپے آپ کے جاز کیش اکاؤنٹ میں بھیجے جائیں گے۔
(آزمائشی پیغام، یہ اصل رقم نہیں ہے)

Aab o Hisaab: High flood risk in Dadu. (Warning)
Get ready to move children, elders and animals to high ground. Listen to official announcements.
You will get Rs 7,000 in your JazzCash account.
(DEMO message, this is not real money)
```

### PAYMENT_NOTICE to beneficiaries (`payment_notice`)

The only beneficiary message that asks for a reply (`expects_reply = true`).

```text
آب و حساب: {amount} روپے آپ کے {wallet_ur} اکاؤنٹ میں بھیج دیے گئے ہیں۔
رقم {wallet_ur} ایجنٹ کی دکان یا ایپ سے نکالیں۔
یہ رقم مفت ہے۔ کسی کو فیس یا اپنا پن نہ دیں۔
رقم مل گئی تو 1 بھیجیں۔ نہیں ملی تو 2 بھیجیں۔
حوالہ: {ref}
[line.demo]

Aab o Hisaab: Rs {amount} has been sent to your {wallet_en} account.
Cash it at a {wallet_en} agent shop or in the app.
This money is free. Never pay a fee or share your PIN.
Got the money? Reply 1. Not received? Reply 2.
Ref: {ref}
[line.demo]
```

`{ref}` is the first 8 characters of the payment id, upper-case. It lets a field agent or the DC office find the payment without any personal data in the message.

### TIER_ALERT to the DC: tier change (`dc.tier_change.up`, `dc.tier_change.up_pending`, `dc.tier_change.down`, `dc.event_closed`)

`dc.tier_change.up` (Watch or Warning, released automatically):

```text
آب و حساب | ضلع {district_ur}
خطرہ {score}/100۔ نیا درجہ: {tiers_ur}۔
جاری رقم: {release_amount} روپے ({release_pct}%)۔
مقصد: {purposes_ur}۔
تفصیل: {link}

Aab o Hisaab | {district} district
Risk {score}/100. New tier: {tiers_en}.
Released: Rs {release_amount} ({release_pct}% of the fund).
Purpose: {purposes_en}.
Details: {link}
```

`dc.tier_change.up_pending` (Action fired; lower tiers in the same run are listed as released):

```text
آب و حساب | ضلع {district_ur}
خطرہ {score}/100۔ درجہ: فوری خطرہ۔
{action_amount} روپے ({action_pct}%) آپ کی منظوری کے بعد جاری ہوں گے۔
منظوری کا پیغام الگ سے آ رہا ہے۔
{released_line_ur}
تفصیل: {link}

Aab o Hisaab | {district} district
Risk {score}/100. Tier: Action.
Rs {action_amount} ({action_pct}%) will be released only after your approval.
A separate approval message follows.
{released_line_en}
Details: {link}
```

`{released_line_*}` is empty, or "پہلے سے جاری: {amount} روپے ({tiers_ur})" / "Already released: Rs {amount} ({tiers_en})".

`dc.tier_change.down`:

```text
آب و حساب | ضلع {district_ur}
خطرہ کم ہو کر {score}/100 ہو گیا ہے (اب: {tier_ur})۔
جاری شدہ رقم واپس نہیں لی جائے گی۔ باقی رقم فنڈ میں رہے گی۔

Aab o Hisaab | {district} district
Risk fell to {score}/100 (now: {tier_en}).
Money already released is not taken back. The rest stays in the fund.
```

`dc.event_closed`:

```text
آب و حساب | ضلع {district_ur}
{days} دن سے خطرہ کم ہے۔ یہ سیلابی واقعہ بند کر دیا گیا ہے۔

Aab o Hisaab | {district} district
Risk has stayed low for {days} days. This flood event is now closed.
```

### APPROVAL_REQUEST to the DC (`dc.approval_request`)

```text
آب و حساب | منظوری درکار
ضلع {district_ur}: خطرہ {score}/100 (فوری خطرہ)۔
رقم: {amount} روپے، {beneficiary_count} افراد کے لیے۔
وجہ: {explanation_ur}
منظوری کے لیے یہ بھیجیں:
APPROVE {code}
رد کرنے کے لیے یہ بھیجیں:
REJECT وجہ
کوڈ {expires_pkt} تک درست ہے۔ تفصیل: {link}

Aab o Hisaab | Approval needed
{district} district: risk {score}/100 (Action tier).
Amount: Rs {amount} for {beneficiary_count} beneficiaries.
Why: {explanation_en}
To approve, send:
APPROVE {code}
To reject, send:
REJECT <reason>
Code valid until {expires_pkt} PKT. Details: {link}
```

`{explanation_*}` is the stored template explanation (D2), cut to 200 characters at a word boundary. The command lines stand alone so right-to-left text does not reorder them.

### ESCALATION (`escalation.pdma`, `escalation.dc_reminder`)

`escalation.pdma` to each active PDMA officer (D6):

```text
آب و حساب | توجہ
ضلع {district_ur} کی فوری رقم ({amount} روپے) کی منظوری {waited_h} گھنٹے سے رکی ہوئی ہے۔
ڈی سی نے ابھی جواب نہیں دیا۔
صرف ڈی سی منظوری دے سکتا ہے۔ براہ کرم ڈی سی سے رابطہ کریں۔
تفصیل: {link}

Aab o Hisaab | Attention
The Action release for {district} (Rs {amount}) has waited {waited_h} hours for approval.
The DC has not replied yet.
Only the DC can approve. Please contact the DC.
Details: {link}
```

`escalation.dc_reminder` to the DC:

```text
آب و حساب | یاد دہانی
ضلع {district_ur} کی فوری رقم کی منظوری ابھی باقی ہے۔ پی ڈی ایم اے کو اطلاع دے دی گئی ہے۔
{code_line_ur}

Aab o Hisaab | Reminder
The Action release for {district} still needs your decision. PDMA has been informed.
{code_line_en}
```

`{code_line_*}` is "منظوری: APPROVE {code}" / "To approve: APPROVE {code}" when FR8 supplies a fresh code, otherwise "ڈیش بورڈ پر فیصلہ کریں: {link}" / "Decide on the dashboard: {link}".

### DECISION_NOTICE to the PDMA officer (`pdma.approval_rejected`, `pdma.approval_approved`)

```text
آب و حساب | ضلع {district_ur}
ڈی سی نے فوری رقم ({amount} روپے) رد کر دی ہے۔
وجہ: {reason}
رقم فنڈ میں موجود ہے۔ تفصیل: {link}

Aab o Hisaab | {district} district
The DC rejected the Action release (Rs {amount}).
Reason: {reason}
The money stays in the fund. Details: {link}
```

```text
آب و حساب | ضلع {district_ur}
ڈی سی نے فوری رقم ({amount} روپے) منظور کر دی ہے۔ ادائیگیاں شروع ہو رہی ہیں۔

Aab o Hisaab | {district} district
The DC approved the Action release (Rs {amount}). Payments are starting.
```

### OUTCOME_SURVEY placeholder (`outcome_survey.q1`, owned by 022, behind `FR22_OUTCOME`)

```text
آب و حساب: کیا اس رقم سے سیلاب سے پہلے آپ کو مدد ملی؟
ہاں تو 1 بھیجیں۔ نہیں تو 2 بھیجیں۔
آپ کا نام کسی کو نہیں بتایا جائے گا۔

Aab o Hisaab: Did this money help you before the flood?
Reply 1 for yes. Reply 2 for no.
Your name will not be shared.
```

Reply acknowledgements and DC command results are specified in `015-fr15-receipt-confirmation/spec.md`. SMS texts are in `016-fr16-sms-field-fallback/spec.md`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR14-001**: System MUST provide one way for any part of the system to request a message: recipient, purpose, template key, context values and related records. The request is stored as a queued alert in the same transaction as the business change that caused it.
- **FR14-002**: System MUST support these purposes: `TIER_ALERT` (to beneficiaries and to DCs), `PAYMENT_NOTICE`, `APPROVAL_REQUEST`, `ESCALATION`, `OUTCOME_SURVEY` (reserved), plus `REPLY_ACK` and `DECISION_NOTICE` (proposed additions).
- **FR14-003**: Every message MUST be rendered from the catalogue with the Urdu block first, then a blank line, then the English block (NFR10). The rendered text MUST be stored on the alert.
- **FR14-004**: Beneficiary templates MUST stay at a low-literacy level: at most 6 lines and 60 words per language block, no technical terms from a blocklist, no tier numbers.
- **FR14-005**: Messages MUST NOT contain a name, CNIC, wallet id or phone number (NFR6, NFR8). Payments are identified by a short reference only.
- **FR14-006**: Every message about simulated money or a simulated beneficiary MUST include the demo label in both languages (NFR13).
- **FR14-007**: System MUST resolve recipients: a beneficiary by record; DCs as all active DC users of the district; PDMA officers as all active PDMA officer users.
- **FR14-008**: When the recipient has no usable phone (missing, not a Pakistani mobile, or already flagged invalid), the alert MUST be stored as `FAILED` with a reason and no provider call.
- **FR14-009**: A background sender MUST pick up queued alerts in priority order (officials first, then beneficiary tier alerts, then payment notices, then other) and hand them to the provider so that `sent_at − triggered_at ≤ 5 min` (NFR2). Official alerts target ≤ 60 s.
- **FR14-010**: Transient provider errors MUST be retried with backoff (10 s, 30 s, 60 s, 120 s; at most 5 attempts). After the last attempt, or on a permanent error, the alert becomes `FAILED` with the provider error code.
- **FR14-011**: Enqueue MUST be idempotent: the same purpose, recipient and related records give the same alert, so re-running a trigger, a payment hook or a timer never sends twice.
- **FR14-012**: When several tiers fire in one evaluation (D4), the DC MUST get one tier-change message and each beneficiary one tier alert, for the highest tier.
- **FR14-013**: Tier alerts MUST include an amount only when the beneficiary is funded and the release is not pending approval. `UNMET_NEED` beneficiaries get safety advice only.
- **FR14-014**: System MUST accept delivery-status reports from the provider only with a valid provider signature, MUST map them to `SENT`, `DELIVERED`, `READ` or `FAILED`, MUST never move a status backwards, and MUST ignore repeats and unknown message ids.
- **FR14-015**: When the provider reports an invalid number for a beneficiary, the system MUST set the beneficiary's phone as invalid and record why, so the record is flagged for update (UC10-1a). Configuration errors (recipient not in sandbox, template required) MUST NOT flag the beneficiary.
- **FR14-016**: Every alert state change (queued, sent, status change, failed) and every phone flag MUST be audited in the same transaction, with no PII in the payload (Principle III, NFR7).
- **FR14-017**: Every alert MUST carry the scenario id of the run that created it, and its audit entries MUST go to that scenario's chain (FR5 isolation).
- **FR14-018**: System MUST have a safe sending mode: in dry-run mode no message leaves the system; in live mode only allowlisted numbers, or non-simulated recipients when explicitly enabled, get a real message. All other alerts are marked simulated and complete as if sent.
- **FR14-019**: For production, each template key MUST be mappable to a pre-approved WhatsApp template; when a mapping exists it is used instead of free text.
- **FR14-020**: Officers and admins MUST be able to list alerts (filters: purpose, status, channel, recipient type, release, payment, approval, date range), view one alert, see stats for a release (counts by status, latency p50/p95/max, NFR2 breaches) and preview templates. DCs see their own district only. Donors have no access.
- **FR14-021**: An admin MUST be able to send a test message to a staff user; an officer or the district's DC MUST be able to re-send a failed alert as a new linked alert.
- **FR14-022**: The `OUTCOME_SURVEY` purpose MUST be refused unless the FR22 feature flag is on.

### Key Entities

- **Alert** (data-model §7): one outbound message to one recipient, with purpose, channel, language `UR_EN`, template key, rendered text, status, provider message id, related payment / release / approval, timestamps, fallback link and scenario id. FR14 proposes queue fields (see plan.md).
- **Beneficiary** (§5): recipient; `phone`, `phone_hash`, `phone_valid`, `preferred_language`, `wallet_provider`, `type`, `simulated`.
- **User** (§8): DC and PDMA officer recipients; `phone`, `phone_hash`, `role`, `district_id`, `is_active`.
- **FundRelease**, **TriggerEvent**, **RiskIndex**, **Allocation**, **Payment**, **Approval**: read for message content.
- **AuditLog** (§8): one entry per alert state change.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (NFR2): For a burst of 5 000 beneficiary alerts and 2 official alerts for one district, 100% of official alerts are sent within 60 s and 100% of all alerts within 300 s of `triggered_at` (dry-run sender with 50 ms simulated provider latency).
- **SC-002** (NFR2, live): In the demo rehearsal on the Twilio sandbox, the DC's approval request arrives on the phone within 60 s of the Action tier firing.
- **SC-003** (NFR10): 100% of catalogue keys render Urdu first; 100% of beneficiary templates pass the line, word and blocklist lint.
- **SC-004**: Re-running the same trigger, payment hook or escalation timer 3 times creates 0 extra alerts.
- **SC-005** (NFR7): 100% of alert state changes and phone flags have one audit entry; the chain verifies.
- **SC-006** (NFR13): 100% of messages about simulated payments or beneficiaries contain the demo label in both languages.
- **SC-007**: 0 real sends to numbers outside the allowlist in dry-run or allowlist mode (test with a recording fake provider).
- **SC-008** (UC10-1a): an invalid-number report flags the beneficiary in the same request, 100% of the time.
- **SC-009** (NFR1): `GET /alerts` p95 < 500 ms with 50 000 alerts.
- **SC-010** (NFR8): 0 phone numbers, names or message texts in audit payloads; donors get 403 on every alerts endpoint.

## Assumptions

- The demo uses the Twilio WhatsApp sandbox. Every real recipient (2 DCs, the PDMA officer, the hero shopkeeper) joins the sandbox and sends a message within 24 hours before the demo.
- In production, business-initiated messages need WhatsApp templates approved in advance; the content above is written so each block can become a template with variables.
- `ReplayClock.now()` (FR5) returns the scenario date plus the current wall-clock time of day, so `sent_at − triggered_at` stays a real elapsed time during a replay step.
- The register's phones are synthetic (FR10); only allowlisted demo phones receive real messages.
- District Urdu names are seeded (proposed `District.name_ur`); the English name is the fallback.
- WhatsApp shows Urdu right-to-left correctly when each line is a single direction or ends with a Latin token on its own line.

## Traceability

| Source | Covered by |
|---|---|
| UC10 main flow step 1 (WhatsApp in Urdu and English: tier, safety advice, amount, how to collect) | US2, US3, FR14-003, FR14-013, catalogue |
| UC10 alternate 1a (invalid number flagged) | US4, FR14-015 |
| UC10 precondition (phone on record; Paid or tier alert due) | FR14-008 |
| UC08 step 5 (start UC10 for each paid beneficiary) | US2 |
| UC07 trigger (approval request on dashboard and WhatsApp) | US1, FR14-009 |
| UC07 alternate 3a (PDMA notified on rejection) | US5 |
| UC07 alternate 1a (escalation) as amended by D6 | US5 |
| UC04 step 6 (tier rose → release flow) | US1, US3 |
| NFR2 (alerts within 5 min) | FR14-009, FR14-010, SC-001, SC-002 |
| NFR6, NFR8 | FR14-005, FR14-020, SC-010 |
| NFR7 | FR14-016, SC-005 |
| NFR10 | FR14-003, FR14-004, SC-003 |
| NFR12 | US1 (approval request) |
| NFR13 | FR14-006, FR14-018, SC-006 |
| D3 (WhatsApp `APPROVE <code>` / `REJECT <reason>`) | `dc.approval_request` |
| D4 (incremental tiers, no clawback) | FR14-012, `dc.tier_change.down` |
| D6 (escalate to PDMA officer) | US5 |
