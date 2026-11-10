# Backend specs: Aab o Hisaab AI

- **Constitution**: [`.specify/memory/constitution.md`](../.specify/memory/constitution.md) (v1.0.0, decisions D1–D6)
- **Authoring rules**: [`_authoring-guide.md`](_authoring-guide.md)
- **Canonical data model**: [`000-foundation/data-model.md`](000-foundation/data-model.md)

Each folder has these files, read in this order:

| File | What it holds |
|---|---|
| `agent.md` | Who builds it and its boundaries |
| `spec.md` | What and why |
| `clarify.md` | Decisions and open items |
| `plan.md` | How |
| `tasks.md` | The checklist |
| `implement.md` | The build guide |

## Index

| # | Folder | Area | Priority | Owner |
|---|---|---|---|---|
| 000 | [000-foundation](000-foundation/) | Platform | MVP (blocking) | Backend & alerts |
| FR1 | [001-fr01-nasa-power-ingestion](001-fr01-nasa-power-ingestion/) | Data | MVP | Data & ML |
| FR2 | [002-fr02-earth-engine-ingestion](002-fr02-earth-engine-ingestion/) | Data | MVP | Satellite & GIS |
| FR3 | [003-fr03-flood-risk-score](003-fr03-flood-risk-score/) | Forecasting | MVP | Data & ML |
| FR4 | [004-fr04-flood-modes](004-fr04-flood-modes/) | Forecasting | MVP | Data & ML |
| FR5 | [005-fr05-event-replay](005-fr05-event-replay/) | Forecasting | MVP | Data & ML |
| FR6 | [006-fr06-fund-tier-config](006-fr06-fund-tier-config/) | Triggers | MVP | Backend & alerts |
| FR7 | [007-fr07-auto-tier-release](007-fr07-auto-tier-release/) | Triggers | MVP | Backend & alerts |
| FR8 | [008-fr08-dc-action-approval](008-fr08-dc-action-approval/) | Triggers | MVP | Backend & alerts |
| FR9 | [009-fr09-beneficiary-split](009-fr09-beneficiary-split/) | Triggers | MVP | Backend & alerts |
| FR10 | [010-fr10-beneficiary-register](010-fr10-beneficiary-register/) | Beneficiaries | MVP | Backend & alerts |
| FR11 | [011-fr11-beneficiary-ranking](011-fr11-beneficiary-ranking/) | Beneficiaries | MVP | Backend & alerts |
| FR12 | [012-fr12-amount-rules](012-fr12-amount-rules/) | Beneficiaries | MVP | Backend & alerts |
| FR13 | [013-fr13-llm-explanations](013-fr13-llm-explanations/) | Beneficiaries | Roadmap | Data & ML |
| FR14 | [014-fr14-whatsapp-alerts](014-fr14-whatsapp-alerts/) | Communication | MVP | Backend & alerts |
| FR15 | [015-fr15-receipt-confirmation](015-fr15-receipt-confirmation/) | Communication | MVP | Backend & alerts |
| FR16 | [016-fr16-sms-field-fallback](016-fr16-sms-field-fallback/) | Communication | Roadmap | Backend & alerts |
| FR17 | [017-fr17-payment-tracking](017-fr17-payment-tracking/) | Payments | MVP | Backend & alerts |
| FR18 | [018-fr18-payment-failure-retry](018-fr18-payment-failure-retry/) | Payments | MVP | Backend & alerts |
| FR19 | [019-fr19-donor-report](019-fr19-donor-report/) | Reporting | MVP | Backend & alerts |
| FR20 | [020-fr20-audit-log](020-fr20-audit-log/) | Reporting | MVP | Backend & alerts |
| FR21 | [021-fr21-satellite-verification](021-fr21-satellite-verification/) | Reporting | Roadmap (D1 part is MVP) | Satellite & GIS |
| FR22 | [022-fr22-outcome-reports](022-fr22-outcome-reports/) | Reporting | Roadmap | Backend & alerts |

## Build order

```
000-foundation ─▶ 020 audit ─┬─▶ 001, 002 data ─▶ 003, 004 forecast ─▶ 005 replay
                             ├─▶ 006, 009 config ─▶ 007 triggers ─▶ 008 approval
                             ├─▶ 010 register ─▶ 011 ranking ─▶ 012 amounts
                             ├─▶ 017 payments ─▶ 018 retry
                             ├─▶ 014 alerts ─▶ 015 confirmations
                             └─▶ 019 donor report
Roadmap after the MVP demo works: 013, 016, 021 (live), 022
```

The critical path for the demo is 000 → 020 → 001/002 → 003/004 → 006 → 007 → 010 → 011/012 → 017 → 014/015 → 008 → 005 → 019.

## Using spec-kit commands with these specs

These folders were authored up front, so the `/speckit-*` commands refine them rather than create them:

- `/speckit-clarify` on a folder: works through its open items
- `/speckit-analyze`: checks spec, plan and tasks consistency across a folder
- `/speckit-implement`: executes `tasks.md` (read `implement.md` first)
