---
description: "Task list for FR13 LLM Explanations (Roadmap: MVP stub + roadmap build)"
---

# Tasks: FR13 LLM Explanations

**Input**: Design documents from `specs/013-fr13-llm-explanations/`

**Prerequisites**: plan.md, spec.md, clarify.md; foundation `flags.is_enabled`, `register_step`; FR3 templates and `RiskExplainInputs`; FR11 reason templates (for US3)

**Tests**: Guardrail tests are mandatory (they protect the explanation of money decisions); write them first.

## Format: `[ID] [P?] [Story] Description`

---

# Part A — MVP stub (behind `FR13_LLM`, default off)

Build only this part in the 32-hour MVP (Principle X).

## Phase A1: Setup

- [ ] T001 [P] `FR13Settings` (flag name, models, timeouts, budget, `FR13_IN_REPLAY`) in `app/services/explain/llm_settings.py`
- [ ] T002 [P] `LLMRewriter` Protocol, `RewriteResult`, `NullRewriter` in `app/integrations/llm.py`

## Phase A2: User Story 6 - Flag off equals MVP (P1, MVP stub)

**Independent Test**: `pytest tests/unit/explain/test_flag_off.py tests/contract/test_explanations_api.py -k disabled`

- [ ] T003 [P] [US6] Test: flag off → step no-op, client spy never called, `display` = template, `llm_status=DISABLED` in `tests/unit/explain/test_flag_off.py`
- [ ] T004 [P] [US6] Contract test for GET risk/allocation explanation with the flag off (shape, roles, DONOR 403) in `tests/contract/test_explanations_api.py`
- [ ] T005 [US6] `display_explanation(row)` and `LLMExplainService` skeleton (returns template when flag off) in `app/services/explain/llm_explainer.py`
- [ ] T006 [US6] GET `/api/v1/explanations/risk/{id}` and `/allocations/{id}` in `app/api/v1/explanations.py`; schemas in `app/schemas/explanations.py`; register router
- [ ] T007 [US6] Register no-op step `register_step(45, "explain_llm", …)` in `app/jobs/steps/explain_llm.py`

**Checkpoint (MVP)**: endpoints live, flag off, zero model calls.

---

# Part B — Roadmap build

## Phase B1: Foundational

- [ ] T008 Migration `alembic/versions/0013_fr13_llm_explanations.py` with the proposed status/model/hash columns on `risk_index` and `allocation`
- [ ] T009 [P] Write `tests/fixtures/explain/cases.jsonl`: 40 risk cases (both modes, all tiers, fallback/stale/proxy flags) and 20 allocation cases (household/shopkeeper, capped, unmet), each with EN/UR template + facts
- [ ] T010 [P] Prompts and glossary (`risk-v1`, `alloc-v1`), `build_user_message()` with field whitelist in `app/services/explain/llm_prompts.py`

## Phase B2: User Story 2 - Guardrails (P1)

**Independent Test**: `pytest tests/unit/explain/test_guardrails.py`

### Tests (write first)
- [ ] T011 [P] [US2] Number tests: invented number rejected; Urdu digits 82 (Urdu digits) and Arabic-Indic 82 (Arabic-Indic digits) accepted for 82; "15,000", "15 [Urdu text]", "1.5 lakh" vs 150000; number words ("two", "[Urdu text]"); date parts (24, 8, 2022, "24-08"); percent 0.91 ↔ 91; "82/100" in `tests/unit/explain/test_guardrails.py`
- [ ] T012 [P] [US2] Content tests: missing score, tier mismatch (EN and UR labels), Urdu slot in English, too long, URL, phone-like digits, markdown, two line breaks in `tests/unit/explain/test_guardrails.py`
- [ ] T013 [P] [US2] Whitelist test: allocation facts never include name/phone/cnic/wallet_id/lat/lon even if present on the row in `tests/unit/explain/test_guardrails.py -k whitelist`

### Implementation
- [ ] T014 [US2] `normalise_digits`, `extract_numbers`, `allowed_numbers`, `check(text, template, facts, kind, language, tier)` → `CheckResult(ok, violations)` in `app/services/explain/guardrails.py`

## Phase B3: User Story 1 - Risk rephrasing (P1)

**Independent Test**: `pytest tests/integration/explain/test_llm_step.py -k risk`

### Tests (write first)
- [ ] T015 [P] [US1] Rewriter test with an injected fake client: request has `system` with `cache_control`, `output_config.effort=low`, no `temperature`; text extracted from `text` blocks only; `stop_reason != "end_turn"` → ERROR in `tests/unit/explain/test_llm_rewriter.py`
- [ ] T016 [US1] Step test (flag on, fake client): accepted EN stored with model/prompt version/hash; rejected UR keeps template and violations; notices appended verbatim; audit row per language in `tests/integration/explain/test_llm_step.py`

### Implementation
- [ ] T017 [US1] `AnthropicRewriter.rewrite(template, facts, language, kind)` with timeouts, `max_retries=1`, error chain → `RewriteResult` in `app/integrations/llm.py`
- [ ] T018 [US1] `LLMExplainService.rewrite_risk(db, ri)` (facts from `RiskExplainInputs`, cache, check, append notices, store, audit) in `app/services/explain/llm_explainer.py`
- [ ] T019 [US1] Real step body: today's district RiskIndex rows for the scenario, thread pool (4), overall budget in `app/jobs/steps/explain_llm.py`

## Phase B4: User Story 4 - Never block money (P1)

**Independent Test**: `pytest tests/integration/explain/test_llm_step.py -k "timeout or budget or nokey"`

- [ ] T020 [US4] Tests: fake client sleeping past timeout → ERROR timeout, forecast and FR7 results unchanged; budget exhausted → remaining `NONE`; no API key → `ERROR: NO_API_KEY`, no call; replay scenario with `FR13_IN_REPLAY=false` → skip, in `tests/integration/explain/test_llm_step.py`
- [ ] T021 [US4] Budget, replay skip and key check in `app/jobs/steps/explain_llm.py`; start-up warning in `app/services/explain/llm_settings.py`

## Phase B5: User Story 3 - Allocation reasons (P2)

**Independent Test**: `pytest tests/integration/explain/test_llm_step.py -k allocation`

- [ ] T022 [US3] Tests: first view rewrites and stores; second view no call (same input hash); amount not in inputs → template in `tests/integration/explain/test_llm_step.py`
- [ ] T023 [US3] `LLMExplainService.rewrite_allocation(db, alloc)` (lazy, 8 s) and hook into GET allocation explanation in `app/services/explain/llm_explainer.py`, `app/api/v1/explanations.py`

## Phase B6: User Story 5 - Admin and audit (P3)

**Independent Test**: `pytest tests/contract/test_explanations_api.py -k "regenerate or status"`

- [ ] T024 [P] [US5] Contract tests: regenerate (ADMIN only; 409 flag off; 503 no key), llm-status counts in `tests/contract/test_explanations_api.py`
- [ ] T025 [US5] POST regenerate and GET llm-status in `app/api/v1/explanations.py`

## Phase B7: Polish

- [ ] T026 `scripts/explain_eval.py` against the real API on `cases.jsonl`; write acceptance by language and violation counts to `reports/fr13_eval.md`
- [ ] T027 Native-speaker review of 20 Urdu outputs; tune prompt → bump `prompt_version`
- [ ] T028 [P] Nightly re-check job: re-run `guardrails.check` on stored ACCEPTED texts (SC-001) in `app/jobs/steps/explain_llm.py`
- [ ] T029 `ruff`, `mypy --strict app/services/explain app/integrations/llm.py`

## Dependencies & Execution Order

- Part A: T001/T002 → T003/T004 → T005–T007. Needs only FR3 template columns.
- Part B: T008–T010 → US2 (guardrails) → US1 → US4 → US3 → US5 → Polish. US3 needs FR11 reasons; T026/T027 need an API key.

## Parallel opportunities

- T001/T002; T003/T004; T009/T010; T011–T013; T015 with T014; T024 with T022.

## Implementation Strategy

Ship Part A in the MVP. Part B starts with guardrails (the safety core), then the risk step, then the rest. Enable `FR13_LLM` only after T026 and T027 meet the targets.
