# Implementation Guide: FR22 Outcome Reports

FR22 is **roadmap** (Principle X). The MVP builds **Part A only**. Part B is specified so it can be picked up after the hackathon without re-design.

---

## Part A: MVP stub (behind a flag)

### Execution order

| Step | Tasks |
|---|---|
| 1 | T001 flag, T002 package |
| 2 | T003, T004 tests (failing) |
| 3 | T005 schemas, T006 service, T007 router |

### Key code

```python
# app/services/outcomes/service.py
from app.core import flags

class OutcomeStoryService:
    """FR19 calls this. Returns [] when FR22_OUTCOME is off, so FR19 falls back to curated simulated stories."""

    @staticmethod
    def stories(db, *, district_ids: list[UUID], scenario_id: UUID | None = None) -> list[OutcomeStoryOut]:
        if not flags.is_enabled("FR22_OUTCOME"):
            return []
        aggs = aggregate_outcomes(db, district_ids=district_ids, event_id=None, scenario_id=scenario_id,
                                  k=settings.OUTCOME_K_ANON)                     # Part B
        return [s for a in aggs if (s := render_story(a)) is not None]

    @staticmethod
    def aggregates(db, *, district_ids, event_id=None, scenario_id=None) -> list[OutcomeAggregateOut]:
        if not flags.is_enabled("FR22_OUTCOME"):
            return []
        return aggregate_outcomes(db, district_ids=district_ids, event_id=event_id, scenario_id=scenario_id,
                                  k=settings.OUTCOME_K_ANON)
```

In Part A, `aggregate_outcomes` and `render_story` are not yet imported; the flag-on branch raises `NotImplementedError("FR22 roadmap")`. The flag is off in every MVP environment.

```python
# app/api/v1/outcomes.py
def require_flag(name: str):
    def dep():
        if not flags.is_enabled(name):
            raise NotFound(code="FEATURE_DISABLED", detail=f"{name} is not enabled")
    return Depends(dep)

router = APIRouter(prefix="/outcomes", tags=["Reporting"], dependencies=[require_flag("FR22_OUTCOME")])
```

The flag dependency runs **before** role checks, so every role sees `FEATURE_DISABLED` when off (test T004 expects that).

```python
# app/schemas/outcomes.py
class QuestionAggregateOut(DonorScope):
    n: int | None
    suppressed: bool
    shares_pct: dict[str, float] | None

class OutcomeAggregateOut(DonorScope):
    district: DistrictRef            # id + name only
    beneficiary_type: Literal["HOUSEHOLD", "SHOPKEEPER"]
    event: EventRef                  # id + "YYYY-MM"
    suppressed: bool
    n_sent: int | None
    n_completed: int | None
    n_expired: int | None
    response_rate_pct: float | None
    questions: dict[str, QuestionAggregateOut]
    simulated: bool

class OutcomeStoryOut(DonorScope):
    district: str
    beneficiary_type: Literal["HOUSEHOLD", "SHOPKEEPER"]
    event_period: str
    headline_ur: str; headline_en: str
    body_ur: str; body_en: str
    n_respondents: int
    simulated: bool
```

### Contract test sketch (T003)

```python
FORBIDDEN = {"name", "phone", "cnic", "wallet_id", "beneficiary_id", "register_ref", "lat", "lon", "tehsil", "tehsil_id"}

@pytest.mark.parametrize("model", [OutcomeAggregateOut, OutcomeStoryOut, QuestionAggregateOut])
def test_donor_schemas_have_no_beneficiary_fields(model):
    assert issubclass(model, DonorScope)
    keys = set(_all_property_names(model.model_json_schema()))      # walks $defs recursively
    assert not keys & FORBIDDEN
```

### Commands (Part A)

```bash
pytest tests/contract/test_outcomes_donor_scope.py tests/integration/outcomes/test_flag_off.py -q
```

### Verification (Part A)

| Scenario | Verified by |
|---|---|
| US1-1 empty stories | `test_flag_off.py::test_service_returns_empty` |
| US1-2 routes disabled | `test_flag_off.py::test_routes_feature_disabled[role-route]` |
| US1-3 no beneficiary fields | `test_outcomes_donor_scope.py::test_donor_schemas_have_no_beneficiary_fields` |

### Demo notes (Part A)

FR22 does not appear in the 3-minute demo. The donor report shows FR19's curated stories with a **SIMULATED** label. If asked: "Outcome surveys are on the roadmap; the stub and the privacy test are already in place."

---

## Part B: Roadmap build

### Execution order

| Step | Tasks |
|---|---|
| 1. Schema | T008, T009 (after foundation merge) |
| 2. Content | T010, T011 |
| 3. Survey flow | T012–T013 (tests) → T014–T018 |
| 4. Expiry | T019 → T020 |
| 5. Aggregates/stories | T021–T023 (tests) → T024–T028 |
| 6. Monitoring | T029 → T030 |
| 7. Polish | T031–T033 |

### Questions and parsing (`questions.py`)

```python
@dataclass(frozen=True)
class Question:
    no: int
    key: str
    template_key: str
    options: dict[int, str]          # digit -> code

QUESTIONS_V1 = {
    "HOUSEHOLD": [
        Question(1, "evacuated", "outcome_q1_household", {1: "YES", 2: "NO"}),
        Question(2, "spent_on", "outcome_q2", {1: "FOOD", 2: "TRANSPORT", 3: "SHELTER", 4: "MEDICINE", 5: "SHOP_GOODS", 6: "OTHER"}),
        Question(3, "helped", "outcome_q3", {1: "A_LOT", 2: "A_LITTLE", 3: "NOT_AT_ALL"}),
    ],
    "SHOPKEEPER": [
        Question(1, "stock_protected", "outcome_q1_shopkeeper", {1: "ALL", 2: "SOME", 3: "NONE"}),
        Question(2, "spent_on", "outcome_q2", {1: "FOOD", 2: "TRANSPORT", 3: "SHELTER", 4: "MEDICINE", 5: "SHOP_GOODS", 6: "OTHER"}),
        Question(3, "helped", "outcome_q3", {1: "A_LOT", 2: "A_LITTLE", 3: "NOT_AT_ALL"}),
    ],
}

_FIRST_DIGIT = re.compile(r"\d")

def parse_answer(q: Question, body: str) -> ParsedAnswer:
    text = normalise_digits(body or "")            # FR15: \u06f0-\u06f9 and \u0660-\u0669 -> 0-9
    m = _FIRST_DIGIT.search(text)
    if not m or int(m.group()) not in q.options:
        return ParsedAnswer(valid=False, raw=body)
    v = int(m.group())
    return ParsedAnswer(valid=True, value=v, code=q.options[v], raw=body)
```

### Scheduling (`survey.py`)

```python
def schedule_due_surveys(db, now: datetime) -> TickReport:
    if not in_send_window(now, settings.OUTCOME_SEND_WINDOW, tz="Asia/Karachi"):
        return TickReport(skipped_outside_window=True)
    last_paid = (select(Payment.beneficiary_id, FundRelease.event_id,
                        func.max(Payment.paid_at).label("last_paid_at"),
                        func.max(Payment.id).label("payment_id"))     # deterministic pick; refine with window fn
                 .join(Allocation, Allocation.id == Payment.allocation_id)
                 .join(FundRelease, FundRelease.id == Allocation.fund_release_id)
                 .where(Payment.status == "PAID", Payment.scenario_id.is_(current_scenario()))
                 .group_by(Payment.beneficiary_id, FundRelease.event_id)).subquery()
    due = db.execute(select(last_paid, Beneficiary)
                     .join(Beneficiary, Beneficiary.id == last_paid.c.beneficiary_id)
                     .outerjoin(OutcomeReport, and_(OutcomeReport.beneficiary_id == last_paid.c.beneficiary_id,
                                                    OutcomeReport.event_id == last_paid.c.event_id))
                     .where(OutcomeReport.id.is_(None),
                            last_paid.c.last_paid_at <= now - timedelta(days=settings.OUTCOME_SURVEY_DELAY_DAYS),
                            Beneficiary.phone_valid.is_(True), Beneficiary.is_active.is_(True))).all()
    report = TickReport()
    for row in due:
        if pending_payment_notice(db, row.Beneficiary.id, now, hours=settings.OUTCOME_PENDING_NOTICE_WAIT_HOURS):
            report.skipped_pending_notice += 1; continue
        if not sampled(row.Beneficiary.id, row.event_id, settings.OUTCOME_SURVEY_SAMPLE_RATE):  # hash-based, deterministic
            continue
        r = OutcomeReport(beneficiary_id=row.Beneficiary.id, event_id=row.event_id, payment_id=row.payment_id,
                          channel="WHATSAPP", status="SENT", survey_version="outcome-v1", current_question=1,
                          invalid_attempts=0, sent_at=now, last_message_at=now, answers={"version": "outcome-v1"},
                          simulated=row.Beneficiary.simulated, scenario_id=current_scenario())
        db.add(r); db.flush()
        q1 = QUESTIONS_V1[row.Beneficiary.type][0]
        for key in ("outcome_intro", q1.template_key):
            AlertService.enqueue(db, recipient=row.Beneficiary, purpose="OUTCOME_SURVEY", template_key=key,
                                 context={}, related={"outcome_report_id": r.id})
        AuditService.record(db, event_type="OUTCOME_SURVEY_SENT", entity_type="OutcomeReport", entity_id=r.id,
                            actor="system", payload={"beneficiary_id": str(r.beneficiary_id), "event_id": str(r.event_id),
                                                     "payment_id": str(r.payment_id), "survey_version": "outcome-v1",
                                                     "simulated": r.simulated})
        report.started += 1
    return report
```

The unique key `(beneficiary_id, event_id, scenario_id)` makes a concurrent double tick fail safely (catch `IntegrityError` per row with a savepoint).

### Reply handling (`survey.py`)

```python
def handle_reply(db, *, beneficiary_id: UUID, body: str, provider_message_id: str) -> bool:
    if not flags.is_enabled("FR22_OUTCOME"):
        return False
    r = db.scalar(select(OutcomeReport).where(OutcomeReport.beneficiary_id == beneficiary_id,
                                              OutcomeReport.status.in_(("SENT", "IN_PROGRESS")))
                  .order_by(OutcomeReport.sent_at.desc()).limit(1).with_for_update())
    if r is None or r.last_message_at < clock.now() - timedelta(hours=settings.OUTCOME_SURVEY_EXPIRY_HOURS):
        return False
    if r.last_provider_message_id == provider_message_id:
        return True                                                    # duplicate webhook
    b = db.get(Beneficiary, beneficiary_id)
    q = QUESTIONS_V1[b.type][r.current_question - 1]
    ans = parse_answer(q, body)
    r.last_provider_message_id, r.status = provider_message_id, "IN_PROGRESS"
    if not ans.valid and r.invalid_attempts == 0:
        r.invalid_attempts = 1
        _send(db, b, r, "outcome_reask"); _send(db, b, r, q.template_key)
        return True
    r.answers = {**r.answers, f"q{q.no}": {"key": q.key, "value": ans.value, "code": ans.code,
                                         "raw": body[:40], "at": clock.now().isoformat(),
                                         **({"invalid": True} if not ans.valid else {})}}
    r.invalid_attempts, r.current_question = 0, r.current_question + 1
    if r.current_question > len(QUESTIONS_V1[b.type]):
        r.status, r.received_at = "COMPLETED", clock.now()
        _send(db, b, r, "outcome_thanks")
        AuditService.record(db, event_type="OUTCOME_REPORT_COMPLETED", entity_type="OutcomeReport", entity_id=r.id,
                            actor="system", payload={"answered": answered(r), "invalid": invalid(r),
                                                     "answers_sha256": sha256_canonical(codes(r))})
    else:
        _send(db, b, r, QUESTIONS_V1[b.type][r.current_question - 1].template_key)
    return True
```

`raw` is truncated to 40 characters and stored only in the report (officer-only), never in aggregates, stories or audit. `_send` updates `last_message_at`.

### Aggregation (`aggregate.py`)

```python
def aggregate_outcomes(db, *, district_ids, event_id, scenario_id, k: int) -> list[OutcomeAggregateOut]:
    rows = load_reports(db, district_ids, event_id, scenario_id)      # joins Beneficiary.type, Tehsil.district_id
    out = []
    for (district, btype, event), group in groupby_sorted(rows, key=lambda r: (r.district, r.type, r.event)):
        completed = [r for r in group if r.status == "COMPLETED"]
        cell_suppressed = len(completed) < k
        questions = {}
        for q in QUESTIONS_V1[btype]:
            vals = [r.answers.get(f"q{q.no}", {}).get("code") for r in completed]
            vals = [v for v in vals if v]
            if cell_suppressed or len(vals) < k:
                questions[q.key] = QuestionAggregateOut(n=None, suppressed=True, shares_pct=None)
            else:
                c = Counter(vals)
                questions[q.key] = QuestionAggregateOut(
                    n=len(vals), suppressed=False,
                    shares_pct={code: pct1(c[code], len(vals)) for code in q.options.values()})
        n_sent = len(group)
        out.append(OutcomeAggregateOut(
            district=district.ref(), beneficiary_type=btype, event=event.ref(), suppressed=cell_suppressed,
            n_sent=None if cell_suppressed else n_sent,
            n_completed=None if cell_suppressed else len(completed),
            n_expired=None if cell_suppressed else sum(r.status == "EXPIRED" for r in group),
            response_rate_pct=None if cell_suppressed or n_sent < k else pct1(len(completed), n_sent),
            questions=questions, simulated=any(r.simulated for r in group)))
    return out

def pct1(part: int, whole: int) -> float:
    return float((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), ROUND_HALF_UP))
```

Shares are rounded independently, so they may sum to 99.9–100.1; the test allows ±0.1 (or use largest-remainder rounding if the frontend needs exactly 100).

### Stories (`stories.py`)

```python
SPEND_UR = {"FOOD": "[Urdu text]", "TRANSPORT": "[Urdu text]", "SHELTER": "[Urdu text]", "MEDICINE": "[Urdu text]",
            "SHOP_GOODS": "[Urdu text]", "OTHER": "[Urdu text]"}
SPEND_EN = {"FOOD": "food", "TRANSPORT": "transport or moving", "SHELTER": "shelter", "MEDICINE": "medicine",
            "SHOP_GOODS": "the shop or goods", "OTHER": "other needs"}

def render_story(a: OutcomeAggregateOut) -> OutcomeStoryOut | None:
    key = "evacuated" if a.beneficiary_type == "HOUSEHOLD" else "stock_protected"
    q = a.questions.get(key)
    if a.suppressed or q is None or q.suppressed:
        return None
    positive = ("YES",) if key == "evacuated" else ("ALL", "SOME")
    k_pos = round_half_up(sum(q.shares_pct[c] for c in positive) * q.n / 100)    # back to a count
    pct = whole_pct(k_pos, q.n)                                                     # 33/40 -> 83 (half up)
    ...                                                                             # fill templates from plan.md
```

Use counts, not re-rounded percentages, as the source of truth: compute `k_pos` directly from the reports in `aggregate_outcomes` and pass it along (store it in a private field), so 33/40 is exact.

### Simulation script (`scripts/simulate_outcome_responses.py`)

- Inputs: `--scenario sindh-2022 --seed 2022 [--response-rate 0.75]`.
- For each PAID payment's beneficiary-event in that scenario: with probability `response_rate` create a `COMPLETED` report, answers drawn from `data/seeds/outcome_simulation.yaml` (e.g. households `evacuated` YES 0.8; shopkeepers `stock_protected` ALL 0.45 / SOME 0.35 / NONE 0.2; spending mix), else `EXPIRED` with 0–1 answers. All rows `simulated=true`. One `OUTCOME_REPORT_COMPLETED` / `_EXPIRED` audit entry each, `actor="replay-sim"`.
- Never sends messages.

### Configuration

| Key | Default |
|---|---|
| `FR22_OUTCOME` flag | off |
| `OUTCOME_SURVEY_DELAY_DAYS` | 7 |
| `OUTCOME_SURVEY_EXPIRY_HOURS` | 72 |
| `OUTCOME_PENDING_NOTICE_WAIT_HOURS` | 48 |
| `OUTCOME_SEND_WINDOW` | `09:00-19:00` (Asia/Karachi) |
| `OUTCOME_K_ANON` | 5 |
| `OUTCOME_SURVEY_SAMPLE_RATE` | 1.0 |
| `OUTCOME_JOB_INTERVAL_MIN` | 15 |

### Commands (Part B)

```bash
alembic upgrade head                                            # 0022
pytest tests/unit/outcomes -q
pytest tests/integration/outcomes -q
pytest tests/contract/test_outcomes_donor_scope.py -q
FLAGS__FR22_OUTCOME=true python scripts/simulate_outcome_responses.py --scenario sindh-2022 --seed 2022
curl -H "Authorization: Bearer $DONOR" "http://localhost:8000/api/v1/outcomes/stories?district_id=<dadu>"
```

### Verification (Part B)

| Scenario | Verified by |
|---|---|
| US2-1 survey starts after 7 days | `test_survey_flow.py::test_due_after_delay` |
| US2-2 Urdu digit reply | `test_questions_parse.py::test_urdu_digit`, `test_survey_flow.py::test_reply_advances` |
| US2-3 re-ask once, then null | `test_survey_flow.py::test_reask_then_skip` |
| US2-4 completion + thanks + audit | `test_survey_flow.py::test_completion` |
| US2-5 once per event | `test_survey_flow.py::test_idempotent_tick` |
| US2-6 shopkeeper Q1 | `test_survey_flow.py::test_shopkeeper_first_question` |
| US3-1..2 | `test_expiry.py` |
| US4-1..5 | `test_aggregate_kanon.py`, `test_stories.py`, `test_outcomes_donor_scope.py` |
| US5-1..2 | `test_outcomes_donor_scope.py::test_officer_*` |

### Demo notes (Part B)

When built: after the `sindh-2022` replay reaches the payments step, run the simulation script, then open the donor report; FR19 shows "83% of households moved to safety in Dadu" with a **SIMULATED** badge.

### Pitfalls

- Python's `round(82.5)` is 82 (banker's rounding). Use `Decimal.quantize(..., ROUND_HALF_UP)` everywhere percentages are shown.
- Never send a survey while a payment notice awaits a reply; FR15 would mis-route a `1`.
- Counts are identifying in small groups: suppress counts as well as shares below k.
- Survey messages to `simulated=true` beneficiaries must respect FR14's `DEMO_PHONE_ALLOWLIST`.
- Store `raw` replies truncated and keep them out of aggregates, stories, audit and logs.
- Production WhatsApp requires approved templates for business-initiated messages outside the 24-hour session window; the intro message is business-initiated.
