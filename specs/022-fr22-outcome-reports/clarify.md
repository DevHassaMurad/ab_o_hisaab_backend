# Clarifications: FR22 Outcome Reports

## Clarifications

### Session 2026-11-10

- Q: What is the flag name? → A: `FR22_OUTCOME` (authoring guide). The foundation plan's `flags.py` lists `FR22_OUTCOME`; flagged as an open item for the foundation owner to align. Default off.
- Q: What does the MVP stub include? → A: The flag check, `OutcomeStoryService.stories()` / `.aggregates()` returning `[]` when off, the routes answering `404 FEATURE_DISABLED`, the donor-scoped schemas, and the contract test. No migration, no job, no messages. FR19 shows curated simulated stories when the list is empty.
- Q: When is the survey sent? → A: `OUTCOME_SURVEY_DELAY_DAYS` (default **7**) after the beneficiary's **last** PAID payment in the event. Why: enough time to have used the money and for the water to arrive (slow-onset lead is 7 days), short enough to remember. One survey per beneficiary per event.
- Q: What if a payment notice is still waiting for a `1`/`2` reply? → A: The survey waits until it is answered or 48 hours have passed. This keeps FR15's digit routing unambiguous (a `1` is never both "received" and a survey answer).
- Q: Which questions? → A: `outcome-v1`, three single-digit questions, Urdu first:
  - Household Q1 `evacuated`: "Did you move your family to a safe place before the water came?" 1 Yes, 2 No.
  - Shopkeeper Q1 `stock_protected`: "Did you protect your shop's goods before the water came?" 1 All, 2 Some, 3 None.
  - Q2 `spent_on` (both): "What did you spend most of the money on?" 1 Food, 2 Transport or moving, 3 Shelter, 4 Medicine, 5 Shop or goods, 6 Something else.
  - Q3 `helped` (both): "How much did this money help?" 1 A lot, 2 A little, 3 Not at all.
  Why: answers the donor's question ("did anticipatory cash change behaviour?") with codes that aggregate cleanly and carry no personal details.
- Q: Free text? → A: No. Free text can contain names, places and phone numbers, which would need moderation before any donor use. Roadmap+ at most.
- Q: How are replies parsed? → A: Normalise Urdu `0–9` (Urdu digits) and Arabic-Indic `0–9` (Arabic-Indic digits) to `0–9` (reuse FR15's normaliser), take the first digit in the message, check it is a valid option. Invalid → one re-ask ("Please reply with a number from the list"); second invalid → store `null` for that question and move on.
- Q: When does a survey expire? → A: 72 hours after the last message sent with no reply (`OUTCOME_SURVEY_EXPIRY_HOURS`). Partial answers are kept and counted per question.
- Q: When can messages be sent? → A: Survey starts only between 09:00 and 19:00 Asia/Karachi (`OUTCOME_SEND_WINDOW`); follow-up questions are sent immediately after a reply, at any time.
- Q: How are aggregates anonymised? → A: District × beneficiary type × event only. k-anonymity with **k = 5**: any cell with fewer than 5 respondents for a question is returned as `suppressed=true` with no numbers. No tehsil breakdown in the roadmap build. Percentages are rounded to whole numbers in stories and one decimal in aggregates.
- Q: How are stories written? → A: Templates over aggregates (same approach as D2): headline + one or two sentences, Urdu then English, naming the district, the count and the top one or two answers. No LLM.
- Q: Who can see what? → A: Aggregates and stories: officer, admin, DC (own district), donor (districts of funds they contributed to, via `DonorContribution`). Individual reports: officer and admin; DC own district; donors 403. Individual reports show `register_ref`, never name or phone.
- Q: How do replays show outcomes? → A: `scripts/simulate_outcome_responses.py --scenario <key> --seed <n>` creates `COMPLETED` reports with `simulated=true` for paid beneficiaries in that scenario, using configured response distributions. Stories built from them are `simulated=true`.
- Q: What is audited? → A: Survey start, completion, expiry (three event types). Individual answers are not audited one by one (they are not money state changes); the completion entry stores the count of answered questions and a hash of the coded answers.
- Q: Do surveys use SMS fallback (FR16)? → A: No. A non-response is data (response rate), not a failure.

## Resolved by constitution decisions

- **Principle X**: roadmap FR, implemented only to the stub in the MVP, behind a flag.
- **Principle VI / NFR8**: donors see aggregates only, proven by a contract test.
- **Principle VIII**: simulated responses and stories are labelled.
- **D4**: an "event" is a `TriggerEvent`; one survey per beneficiary per event.
- D1, D2 (template stories follow the same idea), D3, D5, D6 do not apply directly.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Flag name `FR22_OUTCOME` (guide) vs `FR22_OUTCOME` (foundation `flags.py`) | `FR22_OUTCOME` | One rename in `flags.py` or in this spec | Foundation owner |
| 2 | Survey delay after last payment | 7 days | Shorter → less outcome to report; longer → recall drops | PDMA officer |
| 3 | k-anonymity threshold | k = 5 | Higher hides more small districts; lower risks re-identification | PDMA officer + privacy reviewer |
| 4 | Question set `outcome-v1` (3 questions) | As listed | More questions lower completion; changes aggregates and stories | Product with field partners |
| 5 | Survey a sample instead of everyone | Everyone (`OUTCOME_SURVEY_SAMPLE_RATE=1.0`) | Sampling cuts WhatsApp cost at scale; needs weighting in aggregates | PDMA officer |
| 6 | Production WhatsApp templates for surveys | Required before live use; sandbox for the demo | Without approval, business-initiated surveys cannot be sent in production | FR14 owner |
