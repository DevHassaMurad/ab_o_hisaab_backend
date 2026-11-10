# Clarifications: FR13 LLM Explanations

## Clarifications

### Session 2026-11-10

- Q: UC04-5 says the LLM writes the explanation; D2 says templates. Which? → A: D2. Templates (FR3, FR11) are always generated and stored; the LLM only **rephrases** them, behind `FR13_LLM`, and the template is the fallback.
- Q: Which provider and models? → A: Anthropic Messages API via the official `anthropic` Python SDK. English: `claude-haiku-5-5` (cheap, fast, enough for short rewording). Urdu: `claude-sonnet-5-5` (better Urdu fluency). Both configurable (`FR13_MODEL_EN`, `FR13_MODEL_UR`). *Why*: the guide names these; the volume is tiny so Sonnet for Urdu costs little.
- Q: Sampling settings? → A: No `temperature` (non-default sampling values are rejected on these models); `output_config={"effort": "low"}` (short rewrite, little reasoning); `max_tokens=1024` (room for any adaptive thinking plus ~150 output tokens). No `thinking` parameter. Determinism comes from the result cache, not sampling.
- Q: Server-side model fallbacks on refusal? → A: Not enabled. The template is the fallback; a refusal is recorded as `ERROR: refusal`.
- Q: What exactly is sent? → A: A system prompt (cached) and a user message with JSON: `{"language", "kind": "risk"|"allocation", "template": <main + reasons sentences without notices>, "facts": {...}}`. Risk facts: district name (+ Urdu name), score, tier, peak lead day, target date, top factors (name, display value), mode. Allocation facts: beneficiary type, tier, rank, rank total, priority score, final amount, the rule inputs (poverty score, family size, exposure, stock value, existing support), cap, override flag. **Never**: name, phone, CNIC, wallet id, coordinates, tehsil below district level for allocations.
- Q: Who appends the fallback/stale/proxy notices? → A: The code, verbatim from the template, after the accepted rephrasing. The model never sees or rewrites them. *Why*: Principles VII and VIII must not depend on a model.
- Q: **Number guardrail definition.** → A:
  1. Normalise: Urdu digits `۰–۹` (U+06F0–06F9) and Arabic-Indic `٠–٩` (U+0660–0669) → ASCII; Arabic decimal `٫` → `.`; thousands separators `,` `٬` and thin spaces removed inside numbers.
  2. Extract numbers with `\d+(?:\.\d+)?`; convert number words (EN zero–twenty, thirty…ninety, hundred; UR ایک، دو، تین، چار، پانچ، چھ، سات، آٹھ، نو، دس … بیس) to numbers; apply scale words after a number (thousand/ہزار ×1 000, lakh/لاکھ ×100 000, million ×1 000 000).
  3. Allowed set = all numbers in the template text ∪ every numeric fact value as written, rounded to 0 and 1 decimals, ×100 for 0–1 fractions (percent display) ∪ date parts (day, month number, year, 2-digit year) ∪ `{1, 3, 7, 14, 24, 30, 48, 100}` only if they appear in the template (window lengths, "/100").
  4. Each extracted number must be in the allowed set (numeric equality after normalisation, e.g. `15000 == 15000.00`).
  Month names are words, not numbers. Ordinals ("2nd") are numbers.
- Q: Other guardrails? → A: Required number present (risk: score; allocation: final amount); expected tier label present (EN or UR glossary) and no other tier label; Urdu output ≥ 60% Arabic-script letters, English ≥ 95% ASCII letters; length ≤ max(1.5 × template length, 300 characters); no URL, `@`, digit run ≥ 7 (phone-like) unless allowed, no markdown (`*`, `#`, backticks), at most one line break. Any failure → `REJECTED` with a list of violation codes.
- Q: When does the risk rewrite run? → A: Pipeline step `explain_llm`, order 45, after FR3's `forecast_score` (40), which has already committed scores and called FR7. District RiskIndex rows only (tehsil explanations are map tooltips; template is enough).
- Q: Allocation reasons: eager or lazy? → A: Lazy, on the first `GET /explanations/allocations/{id}` (or allocation detail view) with the flag on; synchronous with an 8 s timeout, template returned if it fails. *Why*: thousands of allocations per release; officers open a few.
- Q: Timeouts and budget? → A: Per call `FR13_TIMEOUT_S=8`, `max_retries=1`; step budget `FR13_STEP_BUDGET_S=120`; thread pool of 4. Items not reached keep the template (`status=NONE`) and are retried next run.
- Q: Caching? → A: (1) Anthropic prompt caching on the system prompt (`cache_control: ephemeral`); the prompt is written to exceed the 512-token minimum for these models (**verify** in the prompt-caching docs). (2) In-process TTL cache (24 h, 4 096 entries) keyed by `sha256(prompt_version | model | language | template | canonical_json(facts))`. (3) DB: `explanation_llm_input_hash` on the row; same hash and status ACCEPTED → skip.
- Q: Replays? → A: Off by default (`FR13_IN_REPLAY=false`) so backtests stay offline and deterministic; can be enabled for a demo.
- Q: Audit? → A: One `EXPLANATION_LLM_EVALUATED` entry per language per item: `{entity_type, entity_id, language, model, prompt_version, status, violations, text_sha256, input_hash, latency_ms}`. No text in the payload (hash only), so the audit stays small and PII-free.
- Q: Display rule? → A: `display_explanation(row)`: flag on and status ACCEPTED → LLM text (labelled `AI_REPHRASED`); else template (`TEMPLATE`). Responses always include both.
- Q: Missing API key with the flag on? → A: Start-up warning; step records `ERROR: NO_API_KEY` once per run; templates shown.
- Q: Beneficiary messages? → A: Not rephrased (FR14 templates; production WhatsApp business-initiated messages need pre-approved templates anyway).

## Resolved by constitution decisions

- **D2**: templates in the MVP, LLM rewrite roadmap behind a flag, template always the fallback.
- **Principle I**: LLM after commit, never on the money path.
- **Principle VI**: field whitelist, no PII to third parties.
- **Principle X**: roadmap FR implemented only to the MVP stub in the 32-hour build.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Models per language | Haiku 5.5 EN, Sonnet 5.5 UR | Haiku for Urdu cuts cost but may lower acceptance and fluency | Data & ML lead |
| 2 | Show LLM text by default when accepted | Yes, labelled, template one click away | Template-first display reduces benefit | PDMA product owner |
| 3 | Allocation reasons: lazy vs eager | Lazy on first view | Eager multiplies calls by beneficiaries | Data & ML lead |
| 4 | Send allocation rule inputs (poverty score, family size) to a third-party API | Yes, without identifiers | If not allowed, allocation rephrasing is limited to rank, tier and amount | PDMA data-protection owner |
| 5 | Acceptance targets | EN ≥ 90%, UR ≥ 80% | Lower targets → more template fallbacks | Data & ML lead |
| 6 | Enable in replays for the demo | Off | On needs network on stage | Frontend & pitch |
