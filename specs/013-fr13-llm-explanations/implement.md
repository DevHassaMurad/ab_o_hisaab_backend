# Implement Guide: FR13 LLM Explanations

## Execution order

### MVP stub (behind `FR13_LLM`, default off) — build in the 32-hour MVP
| Step | Tasks | Result |
|---|---|---|
| A1 | T001, T002 | Settings, `NullRewriter` |
| A2 | T003, T004 → T005–T007 | Endpoints return template + `DISABLED`; step registered as no-op |

### Roadmap build
| Step | Tasks | Result |
|---|---|---|
| B1 | T008–T010 | Columns, eval cases, prompts |
| B2 | T011–T013 → T014 | Guardrails (safety core) |
| B3 | T015, T016 → T017–T019 | Anthropic rewriter + daily risk step |
| B4 | T020, T021 | Timeouts, budget, replay skip, missing key |
| B5 | T022, T023 | Lazy allocation reasons |
| B6 | T024, T025 | Regenerate + status |
| B7 | T026–T029 | Eval, Urdu review, nightly re-check, lint |

## Key algorithms

### MVP stub (`app/integrations/llm.py`, `app/services/explain/llm_explainer.py`)
```python
@dataclass(frozen=True)
class RewriteResult:
    text: str | None
    status: Literal["OK", "ERROR"]
    model: str | None = None
    error: str | None = None
    latency_ms: int | None = None

class LLMRewriter(Protocol):
    def rewrite(self, *, template: str, facts: dict, language: Literal["en", "ur"], kind: Literal["risk", "allocation"]) -> RewriteResult: ...

class NullRewriter:
    def rewrite(self, **_: Any) -> RewriteResult:
        return RewriteResult(text=None, status="ERROR", error="DISABLED")

def llm_enabled() -> bool:
    if not flags.is_enabled("FR13_LLM"):
        return False
    return current_scenario() is None or settings_fr13().in_replay

def display_explanation(en_tpl: str, ur_tpl: str, en_llm: str | None, ur_llm: str | None,
                        st_en: str | None, st_ur: str | None) -> dict:
    on = flags.is_enabled("FR13_LLM")
    pick = lambda tpl, llm, st: (llm, "AI_REPHRASED") if on and st == "ACCEPTED" and llm else (tpl, "TEMPLATE")
    (en, s_en), (ur, s_ur) = pick(en_tpl, en_llm, st_en), pick(ur_tpl, ur_llm, st_ur)
    return {"en": en, "ur": ur, "source": {"en": s_en, "ur": s_ur}}
```

### Roadmap: Anthropic rewriter (`app/integrations/llm.py`)
```python
import anthropic

class AnthropicRewriter:
    def __init__(self, s: FR13Settings, client: anthropic.Anthropic | None = None):
        self.s = s
        # Reads ANTHROPIC_API_KEY from env (foundation .env key). Timeout in seconds (Python SDK).
        self.client = client or anthropic.Anthropic(timeout=s.timeout_s, max_retries=1)

    def rewrite(self, *, template: str, facts: dict, language: str, kind: str) -> RewriteResult:
        model = self.s.model_ur if language == "ur" else self.s.model_en      # claude-sonnet-5-5 / claude-haiku-5-5
        t0 = time.monotonic()
        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=1024,                              # room for adaptive thinking + ~150 output tokens
                output_config={"effort": "low"},              # short rewrite; no temperature (rejected on these models)
                system=[{"type": "text", "text": SYSTEM_PROMPTS[kind],
                         "cache_control": {"type": "ephemeral"}}],   # stable prefix ≥ 512 tokens (verify minimum)
                messages=[{"role": "user", "content": build_user_message(template, facts, language, kind)}],
            )
        except anthropic.APITimeoutError:
            return RewriteResult(None, "ERROR", model, "TIMEOUT", ms(t0))
        except anthropic.APIConnectionError:
            return RewriteResult(None, "ERROR", model, "CONNECTION", ms(t0))
        except anthropic.RateLimitError:
            return RewriteResult(None, "ERROR", model, "RATE_LIMIT", ms(t0))
        except anthropic.APIStatusError as e:
            return RewriteResult(None, "ERROR", model, f"HTTP_{e.status_code}", ms(t0))
        if resp.stop_reason != "end_turn":                    # refusal, max_tokens, …
            return RewriteResult(None, "ERROR", model, f"STOP_{resp.stop_reason}", ms(t0))
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return RewriteResult(text or None, "OK" if text else "ERROR", model, None if text else "EMPTY", ms(t0))
```
Check `resp.usage.cache_read_input_tokens` in logs: it should be > 0 from the second call of a run; if not, the system prompt is under the minimum or changed.

### Roadmap: guardrails (`app/services/explain/guardrails.py`)
```python
_DIGITS = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u066b", "01234567890123456789.")
_NUM = re.compile(r"\d+(?:[.,\u066c]\d+)*")
_WORDS_EN = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20,
             "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
             "hundred": 100}
_WORDS_UR = {"[Urdu text]": 1, "[Urdu text]": 2, "[Urdu text]": 3, "[Urdu text]": 4, "[Urdu text]": 5, "[Urdu text]": 6, "[Urdu text]": 7, "[Urdu text]": 8, "[Urdu text]": 9, "[Urdu text]": 10,
             "[Urdu text]": 20, "[Urdu text]": 30, "[Urdu text]": 100}
_SCALE = {"thousand": 1_000, "[Urdu text]": 1_000, "lakh": 100_000, "[Urdu text]": 100_000, "million": 1_000_000}
TIER_LABELS = {"en": {"NONE": "Normal", "WATCH": "Watch", "WARNING": "Warning", "ACTION": "Action"},
               "ur": {"NONE": "[Urdu text]", "WATCH": "[Urdu text]", "WARNING": "[Urdu text]", "ACTION": "[Urdu text]"}}

def normalise_digits(s: str) -> str:
    return s.translate(_DIGITS)

def extract_numbers(text: str) -> list[Decimal]:
    t = normalise_digits(text)
    tokens = re.findall(r"\d+(?:[.,\u066c]\d+)*|[^\W\d_]+", t)
    out: list[Decimal] = []
    for i, tok in enumerate(tokens):
        if tok[0].isdigit():
            v = Decimal(tok.replace(",", "").replace("\u066c", ""))
        elif tok.lower() in _WORDS_EN or tok in _WORDS_UR:
            v = Decimal(_WORDS_EN.get(tok.lower(), _WORDS_UR.get(tok, 0)))
        else:
            continue
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        out.append(v * _SCALE.get(nxt, 1))
    return out

def allowed_numbers(template: str, facts: dict) -> set[Decimal]:
    allowed = set(extract_numbers(template))
    for v in iter_scalars(facts):                            # walks dicts/lists
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float, Decimal)) or (isinstance(v, str) and is_number(v)):
            d = Decimal(str(v))
            allowed |= {d, d.quantize(Decimal("1"), ROUND_HALF_UP), d.quantize(Decimal("0.1"), ROUND_HALF_UP)}
            if 0 <= d <= 1:
                allowed.add((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))
        elif isinstance(v, str) and (dt := parse_iso_date(v)):
            allowed |= {Decimal(dt.day), Decimal(dt.month), Decimal(dt.year), Decimal(dt.year % 100)}
    return allowed

def check(text: str, *, template: str, facts: dict, kind: str, language: str, tier: str) -> CheckResult:
    v: list[str] = []
    allowed = allowed_numbers(template, facts)
    for n in extract_numbers(text):
        if n not in allowed:
            v.append(f"NUMBER_NOT_IN_INPUTS:{n}")
    required = Decimal(str(facts["score"] if kind == "risk" else facts["final_amount_pkr"]))
    if required not in set(extract_numbers(text)):
        v.append("MISSING_REQUIRED_NUMBER")
    labels = TIER_LABELS[language]
    if labels[tier] not in text:
        v.append("TIER_MISSING")
    if any(lbl in text for t, lbl in labels.items() if t != tier and lbl != labels[tier]):
        v.append("TIER_MISMATCH")
    letters = [c for c in text if c.isalpha()]
    arabic = sum("\u0600" <= c <= "\u06ff" for c in letters) / max(1, len(letters))
    if (language == "ur" and arabic < 0.60) or (language == "en" and arabic > 0.05):
        v.append("WRONG_LANGUAGE")
    if len(text) > max(int(1.5 * len(template)), 300):
        v.append("TOO_LONG")
    if re.search(r"https?://|www\.|@|[*#`]", text) or re.search(r"\d{7,}", normalise_digits(text)) \
            or text.count("\n") > 1:
        v.append("FORBIDDEN_CONTENT")
    return CheckResult(ok=not v, violations=v)
```
English district names in Urdu text count as non-Arabic letters; the 60% threshold leaves room for them. The phone-like rule exempts numbers that are in `allowed` (large PKR amounts) — implement by checking digit runs against `allowed` first.

### Roadmap: service (`app/services/explain/llm_explainer.py`)
```python
RISK_FACT_KEYS = ("district", "district_ur", "score", "tier", "peak_lead_day", "target_date", "mode", "top_factors")
ALLOC_FACT_KEYS = ("beneficiary_type", "tier", "rank", "rank_total", "priority_score", "final_amount_pkr",
                   "poverty_score", "family_size", "flood_exposure", "stock_value_pkr", "existing_support_pkr",
                   "cap_pkr", "is_override", "status")                   # whitelist; never name/phone/cnic/wallet/lat/lon

class LLMExplainService:
    def __init__(self, rewriter: LLMRewriter, s: FR13Settings):
        self.rw, self.s = rewriter, s
        self.cache: TTLCache[str, RewriteResult] = TTLCache(maxsize=4096, ttl=86400)

    def rewrite_risk(self, db: Session, ri: RiskIndex) -> None:
        facts = {k: v for k, v in risk_facts(ri).items() if k in RISK_FACT_KEYS}
        tpl = {"en": strip_notices(ri.explanation_en, "en"), "ur": strip_notices(ri.explanation_ur, "ur")}
        h = input_hash(PROMPT_VERSION["risk"], tpl, facts)
        if ri.explanation_llm_input_hash == h and ri.explanation_llm_status_en == ri.explanation_llm_status_ur == "ACCEPTED":
            return
        for lang in ("en", "ur"):
            res = self._cached(tpl[lang], facts, lang, "risk", h)
            status, text, viol = self._evaluate(res, tpl[lang], facts, "risk", lang, ri.tier)
            if status == "ACCEPTED":
                text = text + notices_from(ri, lang)                      # verbatim template notices
            setattr(ri, f"explanation_llm_{lang}", text if status == "ACCEPTED" else None)
            setattr(ri, f"explanation_llm_status_{lang}", status)
            setattr(ri, f"explanation_llm_model_{lang}", res.model)
            ri.explanation_llm_violations = {**(ri.explanation_llm_violations or {}), lang: viol}
            AuditService.record(db, event_type="EXPLANATION_LLM_EVALUATED", entity_type="RiskIndex", entity_id=ri.id,
                                actor="system", payload={"language": lang, "model": res.model, "status": status,
                                "prompt_version": PROMPT_VERSION["risk"], "violations": viol, "input_hash": h,
                                "text_sha256": sha256_hex(text) if text else None, "latency_ms": res.latency_ms})
        ri.explanation_llm_prompt_version, ri.explanation_llm_input_hash, ri.explanation_llm_at = \
            PROMPT_VERSION["risk"], h, get_clock().now()

    def _evaluate(self, res, tpl, facts, kind, lang, tier) -> tuple[str, str | None, list[str]]:
        if res.status != "OK" or not res.text:
            return "ERROR", None, [res.error or "EMPTY"]
        chk = check(res.text, template=tpl, facts=facts, kind=kind, language=lang, tier=tier)
        return ("ACCEPTED", res.text, []) if chk.ok else ("REJECTED", None, chk.violations)

    def _cached(self, tpl, facts, lang, kind, h) -> RewriteResult:
        key = f"{h}:{lang}"
        if (hit := self.cache.get(key)) is not None:
            return hit
        res = self.rw.rewrite(template=tpl, facts=facts, language=lang, kind=kind)
        if res.status == "OK":
            self.cache[key] = res
        return res
```
`rewrite_allocation` mirrors this with `ALLOC_FACT_KEYS`, FR11's `reason_en/ur` as templates and `Allocation.reason_llm_*` fields; it runs synchronously inside the GET handler with the same 8 s timeout and commits its own transaction.

### Roadmap: step (`app/jobs/steps/explain_llm.py`)
```python
def explain_llm_step(db: Session, clock: Clock) -> dict:
    if not llm_enabled():
        return {"skipped": "disabled"}
    s = settings_fr13()
    if not s.api_key_present:
        log.warning("fr13_no_api_key"); return {"skipped": "NO_API_KEY"}
    svc = LLMExplainService(AnthropicRewriter(s), s)
    ids = district_risk_index_ids(db, run_date=clock.today(), scenario_id=current_scenario())
    deadline = time.monotonic() + s.step_budget_s
    done = 0
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=s.workers) as pool:
        futures = {pool.submit(ctx.copy().run, _one, svc, rid): rid for rid in ids}   # each worker: own session + commit
        for f in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
            done += f.result()
    return {"rewritten": done, "total": len(ids)}

register_step(45, "explain_llm", explain_llm_step)
```
`as_completed` timeout ends the wait; unfinished items keep the template and are picked up by the next run (`status` stays `NONE`).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FLAGS` | `""` | Include `FR13_LLM` to enable (foundation flag list) |
| `ANTHROPIC_API_KEY` | — | Foundation `.env` key |
| `FR13_MODEL_EN` | `claude-haiku-5-5` | English rewrites |
| `FR13_MODEL_UR` | `claude-sonnet-5-5` | Urdu rewrites |
| `FR13_TIMEOUT_S` | `8` | Per call |
| `FR13_STEP_BUDGET_S` | `120` | Daily step budget |
| `FR13_WORKERS` | `4` | Thread pool |
| `FR13_IN_REPLAY` | `false` | Allow in replay scenarios |
| `FR13_CACHE_TTL_S` | `86400` | In-process cache |

## Commands

```bash
# MVP stub
pytest tests/unit/explain/test_flag_off.py tests/contract/test_explanations_api.py -k disabled -q
# Roadmap build
alembic upgrade head
pytest tests/unit/explain tests/integration/explain tests/contract/test_explanations_api.py -q
FLAGS=FR13_LLM ANTHROPIC_API_KEY=… python scripts/explain_eval.py --cases tests/fixtures/explain/cases.jsonl --out reports/fr13_eval.md
curl -X POST localhost:8000/api/v1/explanations/risk/$RID/regenerate -H "Authorization: Bearer $ADMIN" -d '{"languages":["ur"]}'
curl localhost:8000/api/v1/explanations/llm-status -H "Authorization: Bearer $ADMIN"
```

## Verification checklist

| Acceptance scenario | Check |
|---|---|
| US6-1/2 flag off | T003, T004 (MVP stub) |
| US1-1 stored with model/prompt/status | T016 |
| US1-2 display + template available | T004/T024 shapes, `display_explanation` unit test |
| US1-3 notices preserved | T016 |
| US2-1…5 guardrails | T011, T012 |
| US3-1/2/3 allocation lazy + cache | T022 |
| US4-1/2/3 timeout, budget, no key | T020 |
| US5-1/2/3 regenerate, status | T024 |
| SC-001 nightly re-check | T028 |
| SC-002/003 acceptance + Urdu review | T026, T027 |

## Demo notes

Not in the 3-minute replay (flag off; replays offline). Optional Q&A slide: one district's template vs AI-rephrased Urdu side by side, plus a rejected example ("the model wrote 190 mm, the input says 184 mm, so we showed the template"). That is the answer to "Why trust the model?".

## Pitfalls

- Do not pass `temperature`, `top_p` or `top_k`: non-default values return 400 on these models.
- Do not put the date, run id or facts in the system prompt: it breaks prompt caching. Facts go in the user message.
- Extract text only from `type == "text"` blocks; thinking blocks may appear first.
- Always check `stop_reason` before reading content (`refusal`, `max_tokens`).
- Urdu text mixes scripts (district names in Latin script); tune the 60% threshold on the eval set, not by intuition.
- Never log the full allocation facts at INFO level; they are not identifying but are personal.
- Contextvars (scenario, clock) must be copied into worker threads.
- The tier-label check is conservative: ordinary words such as "action" or the Urdu word for "normal" can cause false `TIER_MISMATCH` rejections; so can Urdu number words that are also common words (`[Urdu text]` "give" "sleep"). Measure on the eval set and drop words from the lists if rejections are mostly false.
