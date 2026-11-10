# Implementation Guide: FR19 Donor report

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T004 | Package, ReportLab, settings, reference fixture with canaries |
| 2 | T005–T006 → T007–T009 | Suppression, DonorScope schemas, scope resolution |
| 3 | T010–T011 → T012–T014 | Headline metrics exact on the reference fixture |
| 4 | T015 → T016–T018 | Breakdowns and payment counts |
| 5 | T019–T021 → T022–T023 | **NFR8 proven** (route sweep, canary scan, scope) |
| 6 | T024 → T025–T026 | PDF with verify + fingerprint, audited |
| 7 | T027 → T028–T030 | Stories |
| 8 | T031–T033 | Perf, docs, lint |

## Key algorithms

### Scope (`app/services/reporting/donor_scope.py`)

```python
@dataclass(frozen=True)
class DonorScopeSpec:
    fund_ids: tuple[UUID, ...]
    districts: tuple[District, ...]
    scenario_id: UUID | None
    contributed_pkr: Decimal | None      # donor only

def resolve_donor_scope(db, user: User, district_id: UUID | None) -> DonorScopeSpec:
    if user.role == Role.DC:
        raise Forbidden(code="ROLE_FORBIDDEN")
    sid = current_scenario()
    if user.role == Role.DONOR:
        links = db.execute(select(DonorContribution.amount_pkr, DisasterFund.district_id)
                           .join(DisasterFund, DisasterFund.id == DonorContribution.fund_id)
                           .where(DonorContribution.donor_id == user.id,
                                  DisasterFund.scenario_id.is_(None))).all()
        if not links:
            raise Forbidden(code="NO_LINKED_FUNDS")
        district_ids = {d for _, d in links}
        contributed = sum((a for a, _ in links), Decimal("0"))
    else:
        district_ids = set(db.scalars(select(DisasterFund.district_id)
                                      .where(DisasterFund.scenario_id.is_(None))))
        contributed = None
    if district_id is not None:
        if district_id not in district_ids:
            raise Forbidden(code="FUND_NOT_LINKED")
        district_ids = {district_id}
    funds = db.scalars(scoped(select(DisasterFund).where(DisasterFund.district_id.in_(district_ids)),
                              DisasterFund)).all()          # live or the replay's funds for the same districts
    return DonorScopeSpec(tuple(f.id for f in funds), tuple(...districts...), sid, contributed)
```

### Metric assembly (`app/services/reporting/donor_metrics.py`)

```python
COUNTED = ("APPROVED", "ALLOCATING", "RELEASED", "PARTIALLY_RELEASED")
Z = Decimal("0.00")

def compute_totals(funds, rel, pay, unmet) -> Totals:
    allocated = sum((f.allocated_pkr for f in funds), Z)
    released = rel.released or Z
    disbursed, received = pay.disbursed or Z, pay.received or Z
    not_received, in_flight = pay.not_received or Z, pay.in_flight or Z
    unused = allocated - disbursed - in_flight
    return Totals(
        allocated_pkr=allocated, released_pkr=released, awaiting_approval_pkr=rel.awaiting or Z,
        disbursed_pkr=disbursed, received_pkr=received, not_received_pkr=not_received,
        awaiting_confirmation_pkr=disbursed - received - not_received,
        in_flight_pkr=in_flight, unused_pkr=unused,
        unmet_need_pkr=unmet.amount or Z, unmet_need_count=unmet.count or 0,
        beneficiaries_reached=pay.reached or 0, beneficiaries_confirmed=pay.confirmed_people or 0,
        disbursement_rate=ratio(disbursed, released), confirmation_rate=ratio(received, disbursed))

def ratio(a: Decimal, b: Decimal) -> float | None:
    return None if b == 0 else round(float(a / b), 3)       # display only; never summed

def ledger_consistent(funds, totals) -> bool:
    return (sum((f.reserved_pkr for f in funds), Z) == totals.in_flight_pkr and
            sum((f.disbursed_pkr for f in funds), Z) == totals.disbursed_pkr and
            sum((f.remaining_pkr for f in funds), Z) == totals.unused_pkr)
```

`summary()` runs the three queries from plan.md (`GROUPING SETS` in one payment query), maps grouping rows by `GROUPING()` bits into totals / district / type / district × type / tier, then calls `suppress_cells` per sibling group and logs `donor_report.ledger_mismatch` (error level) when `ledger_consistent` is false.

### Suppression (`app/services/reporting/suppression.py`)

```python
SUPPRESSIBLE = ("released_pool_pkr", "disbursed_pkr", "received_pkr", "in_flight_pkr",
                "unmet_need_pkr", "beneficiaries_reached", "allocated_pkr", "released_pkr", "unused_pkr")

def suppress_cells(rows: list[dict], k: int) -> list[dict]:
    if k <= 0:
        return rows
    small = [r for r in rows if 0 < r["beneficiaries_reached"] < k]
    for r in small:
        _hide(r)
    if len(small) == 1:                                  # secondary suppression
        others = sorted((r for r in rows if not r.get("suppressed") and r["beneficiaries_reached"] > 0),
                        key=lambda r: r["beneficiaries_reached"])
        if others:
            _hide(others[0])
    return rows

def _hide(r: dict) -> None:
    for f in SUPPRESSIBLE:
        if f in r:
            r[f] = None
    r["suppressed"] = True
```

Sibling groups: all district rows; the type rows inside each district; the top-level type rows; the per-district payment-count rows.

### Stories (`app/services/reporting/stories.py`)

```python
from app.services.audit.pii_guard import PHONE_RE, CNIC_RE   # FR20, alphanumeric-bounded
LONG_DIGITS = re.compile(r"\d{7,}")                             # stories never need 7+ digit runs

class StoryProvider(Protocol):
    def get_stories(self, scope: DonorScopeSpec, scenario_key: str | None) -> list[StoryOut]: ...

def load_curated_stories(path: str) -> list[dict]:
    items = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["stories"]
    for s in items:
        text = " ".join(str(s.get(k, "")) for k in ("title_en", "title_ur", "body_en", "body_ur"))
        if PHONE_RE.search(text) or CNIC_RE.search(text) or LONG_DIGITS.search(text):
            raise ValueError(f"story {s['story_key']} contains PII-like digits")
        if len(s["body_en"]) > 600 or len(s["body_ur"]) > 600:
            raise ValueError(f"story {s['story_key']} too long")
        if s.get("source") != "CURATED" or s.get("simulated") is not True or not s.get("label"):
            raise ValueError(f"story {s['story_key']} must be labelled simulated")
    return items

def get_story_provider() -> StoryProvider:
    return OutcomeStoryProvider() if flags.enabled("FR22_OUTCOME") else CuratedStoryProvider()
```

Load and validate the YAML once at startup (fail fast); the stories test also loads the shipped file.

### PDF (`app/services/reporting/donor_pdf.py`)

```python
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, Spacer
from app.services.audit.canonical import canonical_json

def report_fingerprint(summary: DonorSummaryOut) -> str:
    return hashlib.sha256(canonical_json(summary.model_dump(mode="json")).encode()).hexdigest()

def render_donor_pdf(summary, stories, verify, *, compress: bool) -> tuple[bytes, str]:
    fp = report_fingerprint(summary)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Aab o Hisaab AI - Donor report",
                            pageCompression=1 if compress else 0, invariant=1)
    def on_page(c, d):
        if summary.simulated:
            c.setFillColorRGB(0.75, 0, 0); c.rect(0, A4[1] - 28, A4[0], 28, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1); c.drawCentredString(A4[0] / 2, A4[1] - 19,
                                     "SIMULATED DATA - prototype, no real money moved")
        c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 7)
        c.drawString(36, 20, f"Report fingerprint (SHA-256): {fp}   Page {d.page}")
    story = [Paragraph("Aab o Hisaab AI - Donor report", H1), _scope_par(summary),
             _headline_table(summary.totals), _district_table(summary.by_district),
             _type_table(summary.by_type), _tier_table(summary.by_tier),
             _stories(stories), _audit_box(verify)]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue(), fp

def money(v: Decimal | None) -> str:
    return "< 5" if v is None else f"PKR {v:,.2f}"
```

`invariant=1` makes ReportLab output deterministic (no timestamps or random ids), which helps tests. The fingerprint is over the JSON summary (which includes `as_of`), not over the PDF bytes.

### PDF route (`app/api/v1/reports.py`)

```python
@router.get("/donor/report.pdf")
def donor_pdf(district_id: UUID | None = None, db=Depends(get_db), _sid=Depends(get_scenario),
              user=Depends(require_role(Role.DONOR, Role.PDMA_OFFICER, Role.ADMIN))):
    scope = resolve_donor_scope(db, user, district_id)
    summary = report_service.summary(db, scope)
    stories = get_story_provider().get_stories(scope, scenario_key_of(scope))
    with SessionLocal() as vdb:
        verify = verify_chain(vdb, current_chain_key())
    pdf, fp = render_donor_pdf(summary, stories, verify, compress=settings.DONOR_PDF_COMPRESS)
    get_audit().record(db, event_type="DONOR_REPORT_GENERATED", entity_type="donor_report",
        entity_id=uuid5(NAMESPACE_URL, fp), actor=str(user.id), payload={
            "report_sha256": fp, "district_pcodes": [d.pcode for d in scope.districts],
            "as_of": summary.as_of, "scenario_id": scope.scenario_id, "chain_key": verify.chain_key,
            "chain_head_seq": verify.head_seq, "chain_head_hash": verify.head_hash,
            "chain_valid": verify.valid, "requested_by_role": user.role})
    db.commit()
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="aab-o-hisaab-donor-report-{summary.as_of:%Y%m%d}.pdf"',
        "X-Report-SHA256": fp})
```

Verify runs before the audit entry is written, so the printed head is the head *before* this report's own entry; the audit entry records that same head. That is intentional and documented in the PDF ("chain verified up to entry N").

### Route sweep (`tests/contract/test_donor_route_sweep.py`)

```python
ALLOWLIST = {("POST", "/api/v1/auth/token"), ("GET", "/api/v1/auth/me"), ("GET", "/api/v1/districts"),
             ("GET", "/api/v1/districts/{district_id}"), ("GET", "/api/v1/districts/{district_id}/tehsils"),
             ("GET", "/api/v1/health"), ("GET", "/api/v1/reports/donor/summary"),
             ("GET", "/api/v1/reports/donor/payment-counts"), ("GET", "/api/v1/reports/donor/stories"),
             ("GET", "/api/v1/reports/donor/report.pdf"), ("GET", "/api/v1/audit/verify")}

def donor_can_pass(route) -> bool:
    roles = set()
    for dep in walk_dependencies(route.dependant):          # recursive over route.dependant.dependencies
        roles |= set(getattr(dep.call, "__roles__", ()))
    if getattr(route.endpoint, "__any_authenticated__", False):
        return True
    return not roles or Role.DONOR in roles                  # no role guard = reachable

def test_donor_reachable_routes_are_allowlisted(app):
    reachable = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                 for m in r.methods if donor_can_pass(r) and not r.path.startswith("/api/v1/webhooks")}
    assert reachable <= ALLOWLIST | DENIAL_ROUTES, reachable - ALLOWLIST - DENIAL_ROUTES
```

Webhooks are excluded because they use Twilio signatures, not JWT. Routes with no role guard count as reachable, so a forgotten guard fails the test.

## Configuration

| Key | Default | Notes |
|---|---|---|
| `DONOR_MIN_CELL_SIZE` | `5` | 0 disables suppression |
| `DONOR_STORIES_PATH` | `data/seeds/donor_stories.yaml` | |
| `DONOR_PDF_COMPRESS` | `true` | Tests set `false` to scan bytes |
| `FLAGS` | — | `FR22_OUTCOME` switches the story provider |

## Commands

```bash
pip install -e ".[dev]"                     # picks up reportlab
pytest -q tests/unit/reporting
pytest -q tests/integration/reporting -m "not slow"
pytest -q tests/contract/test_donor_schemas.py tests/contract/test_donor_route_sweep.py tests/contract/test_donor_no_pii.py
curl -H "Authorization: Bearer $DONOR_TOKEN" -H "X-Scenario-Id: $SINDH" localhost:8000/api/v1/reports/donor/summary | jq .totals
curl -OJ -H "Authorization: Bearer $DONOR_TOKEN" localhost:8000/api/v1/reports/donor/report.pdf
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1, US1-2, US1-3, US1-4, SC-002 | `test_metric_definitions.py` + `test_donor_summary.py::test_totals_match_reference` |
| US1-5 | `test_donor_summary.py::test_replay_scope` |
| US2-1, US2-2, US2-4 | `test_donor_summary.py` breakdown/count tests |
| US2-3 | `test_suppression.py` + breakdown test with a 3-person cell |
| US3-1 | `test_donor_route_sweep.py` (denial calls) |
| US3-2, SC-001 | `test_donor_no_pii.py`, `test_donor_schemas.py` |
| US3-3 | `test_donor_route_sweep.py::test_donor_reachable_routes_are_allowlisted` |
| US3-4 | `test_donor_scope.py` |
| US4-1, SC-004 | `test_donor_pdf.py::test_verify_matches_endpoint` |
| US4-2, US4-3, SC-006 | `test_donor_pdf.py` |
| US5-1, US5-2, US5-3, SC-005 | `test_stories.py` + manual read of the shipped YAML |
| SC-003 | `test_perf_50k` + manual PDF timing |

## Demo notes

- Last beat of the live demo (≈30 s): switch to the donor login during the Sindh 2022 replay. Headline cards show allocated / released / disbursed / received / unused for Dadu; the type split shows 70/30; the SIMULATED ribbon is visible.
- Click **Verify chain**: green tick, entry count, short head hash (FR20).
- Click **Download PDF**: open it, point at the fingerprint in the footer and the "Audit chain: VALID" box.
- Optional: as a donor, try to open a payment list; show the 403 "aggregated totals only" message (UC11-3a).
- For the 2021 control replay the report shows no releases and "unused = allocated", which supports the "no false payouts" message.

## Pitfalls

- Never build PDF content from anything but `DonorSummaryOut` and `StoryOut`; that is what keeps the PDF inside the contract test.
- `COUNT(DISTINCT …)` in grouping sets is computed per set; never add up tier or type rows to get "beneficiaries reached", because a person paid in two tiers would be counted twice.
- Coalesce NULL sums to `Decimal("0.00")`; a `None` in Python arithmetic will crash, and a `0` int will serialise as `"0"` instead of `"0.00"`.
- Percentages are display-only floats; never feed them back into money maths.
- ReportLab's built-in Helvetica has no Urdu glyphs; Urdu text in the PDF prints as boxes. Keep the PDF English-only (clarify.md item 3).
- Replay scope: donors link to **live** funds; map to the replay's funds by district, otherwise a replay report is empty.
- The summary must not call `verify_chain` (it is O(n)); only the PDF does, once.
- Keep the deny-list in sync with new schema fields; the schema test fails loudly if someone adds `beneficiary_id` to a donor model.
