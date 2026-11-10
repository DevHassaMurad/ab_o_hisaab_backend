# Agent Brief: FR13 LLM Explanations

## Owner role
**Data & ML**

## Mission
Let a large language model rephrase the system's template explanations of risk scores and payment amounts into clearer English and simpler Urdu, without ever changing a fact. Every number in the LLM text must already be in the inputs, otherwise the template is shown. The template text is always stored and always the fallback (D2). The feature sits behind the flag `FR13_LLM` and never sits on the path that moves money.

## Scope
**MVP stub (behind `FR13_LLM`, default off)**
- Flag check, `NullRewriter`, pipeline step registered as a no-op, explanation endpoints that return the template with `llm_status: DISABLED`.

**Roadmap build**
- `AnthropicRewriter` (`claude-haiku-5-5` for English, `claude-sonnet-5-5` for Urdu), timeouts, retries, prompt caching, in-process result cache.
- Guardrails: numbers ⊆ inputs, required numbers present, tier label correct, language/script check, length, forbidden content. Fallback notices (fallback, stale, proxy) are appended by code, never rewritten.
- Daily step after scoring: rewrite district `RiskIndex` explanations; lazy rewrite of `Allocation` reasons on first view.
- Admin regenerate and status endpoints; audit entry per evaluated rewrite; offline eval script.

**Out**
- Generating explanations from scratch (templates are FR3 / FR11).
- Beneficiary WhatsApp messages (FR14 templates; production needs pre-approved templates anyway).
- Any decision, ranking or amount. The LLM only rephrases.

## Read first
- `.specify/memory/constitution.md` (IV, VI, VII, X; decision D2), `specs/_authoring-guide.md` (FR13 facts)
- `specs/000-foundation/data-model.md` §3 (`RiskIndex.explanation_llm_*`), §5 (`Allocation.reason_*`)
- `specs/003-fr03-flood-risk-score/plan.md` (templates, `RiskExplainInputs`)
- `specs/011-fr11-beneficiary-ranking/plan.md` (allocation reason inputs, when written)
- Anthropic Python SDK docs (Messages API, prompt caching, errors)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | Foundation | `flags.is_enabled`, `register_step`, `ANTHROPIC_API_KEY` setting |
| Depends on | FR3 | Template text + `RiskExplainInputs` |
| Depends on | FR11/FR12 | Allocation reason templates + inputs |
| Depends on | FR20 | Audit |
| Blocks | — | Nothing; every consumer works with the template |

## Interfaces
**Consumes**: `ExplainService` template output (FR3 `TemplateExplainService`, FR11 reasons), `RiskExplainInputs`, `AuditService.record`, `flags.is_enabled("FR13_LLM")`, `register_step(order, name, func)`.
**Provides**: `LLMExplainService.rewrite_risk(db, risk_index)`, `LLMExplainService.rewrite_allocation(db, allocation)`, `display_explanation(row) -> (en, ur, source)` used by FR3/FR8/FR11 read endpoints, pipeline step `explain_llm` (order 45).

## Files you own
```text
app/integrations/llm.py
app/services/explain/llm_settings.py
app/services/explain/llm_prompts.py
app/services/explain/guardrails.py
app/services/explain/llm_explainer.py
app/schemas/explanations.py
app/api/v1/explanations.py
app/jobs/steps/explain_llm.py
alembic/versions/0013_fr13_llm_explanations.py
scripts/explain_eval.py
tests/fixtures/explain/cases.jsonl
tests/unit/explain/test_guardrails.py
tests/unit/explain/test_llm_rewriter.py
tests/unit/explain/test_flag_off.py
tests/integration/explain/test_llm_step.py
tests/contract/test_explanations_api.py
```

## Guardrails
| Principle | How it applies |
|---|---|
| IV Explainable / D2 | Template always stored and shown when the LLM output fails any check; LLM text stored separately with model and prompt version |
| I Anticipate | LLM step runs after scores and triggers are committed; no money path waits for it |
| VI Privacy | Only whitelisted, non-identifying fields are sent (no names, phones, CNIC, wallet ids, coordinates) |
| VII Degrade gracefully | Timeout, refusal, error, missing key → template, status recorded |
| VIII Honest prototype | LLM text labelled "AI-rephrased"; fallback/stale/proxy notices appended verbatim by code |
| X Simplicity | One provider, synchronous calls in a small thread pool, in-process cache |
| III Traceability | `EXPLANATION_LLM_EVALUATED` audit entry with text hash, model, prompt version, result |

## Definition of Done
**MVP stub**
- [ ] With `FR13_LLM` off: no network call, step is a no-op, endpoints return template with `llm_status: DISABLED`.

**Roadmap build**
- [ ] Guardrail unit tests (numbers in Western, Urdu and Arabic-Indic digits, number words, thousand/lakh scaling, tier mismatch, wrong script) all pass.
- [ ] With the flag on and a fake client, the step fills `explanation_llm_*` for accepted outputs and records rejections.
- [ ] Eval on `cases.jsonl`: ≥ 90% accepted for English, ≥ 80% for Urdu; 0 accepted outputs contain a number outside the inputs.
- [ ] Native-speaker review of 20 Urdu outputs: all judged correct and simpler than the template.
- [ ] Daily step for 68 districts completes within its 120 s budget; p95 call latency recorded.

## Hand-off notes
- **FR3 / FR8 / FR11 read endpoints**: call `display_explanation(row)` to pick LLM text (if accepted and flag on) or the template; always also return the template.
- **FR14**: do not use LLM text in beneficiary messages.
- **Pitch**: the "Why trust the model?" answer is the guardrail: "the AI can reword, it cannot change a number".
