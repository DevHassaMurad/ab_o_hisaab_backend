# Clarifications: FR16 SMS and field-agent fallback

## Clarifications

### Session 2026-11-10

- Q: Which messages are chased? → A: Only reply-expecting payment notices (`expects_reply=true`, purpose `PAYMENT_NOTICE`). Tier alerts do not ask for a reply, so "no reply" is meaningless for them; failed Warning/Action tier alerts get an immediate safety SMS in the roadmap build.
- Q: From when are 6 h and 12 h measured? → A: Both from the WhatsApp notice's `sent_at`. UC10 says "no reply within 6 hours … still no reply after 12 hours", which reads as one clock. 12 h is not "6 h after the SMS".
- Q: What counts as a reply? → A: FR15's effective status `CONFIRMED` or `NOT_RECEIVED` on any alert for the same payment, on any channel. `UNPARSEABLE` does not stop the chain.
- Q: What if WhatsApp failed outright? → A: The timed rules need `sent_at`, so they skip never-sent notices. The roadmap build adds immediate rules by error class (not on WhatsApp → SMS now; invalid number → task now).
- Q: Per-alert timers or a sweep? → A: One sweep every 5 minutes. It is restart-safe, catches up after downtime and is trivially idempotent. Per-alert APScheduler jobs would be lost on restart unless persisted.
- Q: Where does the code live? → A: Logic in `app/services/messaging/fallback.py`. The guide says timers live in `app/jobs/timers.py`, so FR16 registers its job there in its own section; FR8 (2 h escalation) and FR17 (24 h stuck) share the file.
- Q: Which SMS provider? → A: MVP stub: dry-run only. Twilio can send SMS to Pakistan, but two-way SMS (replies) through an international long code is unreliable and Pakistan may require sender registration (**verify** current Twilio country rules for PK). Roadmap: a PTA-compliant local aggregator or short code, behind the same `SmsSender` interface.
- Q: How long can the SMS be? → A: Urdu forces UCS-2: 70 characters for one SMS, 67 per part for longer ones. Cap at 3 parts (201 characters). That is why the SMS text is much shorter than WhatsApp's.
- Q: What is a field agent in the MVP? → A: Not a user role. The DC office or PDMA officer sees the task list and (roadmap) records outcomes on the agent's behalf. A `FIELD_AGENT` role is roadmap.
- Q: Why also create a `FIELD_AGENT` channel alert? → A: data-model.md defines the chain "WhatsApp → SMS → field agent" through `Alert.fallback_of_id`. The field-agent alert records the hand-off and carries the brief; a `FieldAgentSender` "sends" it with no network call.
- Q: How does replay interact? → A: Timers use the injected `Clock`, so a replay day step passes both deadlines. Before evaluating, the sweep runs FR15's `simulate_replies` in the scenario so simulated "repliers" are not chased; the ~10% silent ones get SMS then tasks, which demonstrates the chain.
- Q: One task per payment or per beneficiary? → A: Per payment (one open task per payment, partial unique index). Payments are what must be verified.
- Q: Does the task brief include the name and phone? → A: No. It shows the payment reference, tehsil and amount; staff look up contact details in the dashboard under RBAC (Principle VI).

## Resolved by constitution decisions

- Principle X: roadmap feature behind `FR16_FALLBACK`; MVP stub is minimal.
- Principle VII: this is the degrade path for unreachable people; failures are visible as tasks.
- Principle VIII: dry-run SMS and tasks for simulated payments are labelled simulated.
- Principle IX: 6 h, 12 h and intervals are settings.
- Technology Stack: "APScheduler (in-process) for … SMS fallback".

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | SMS route for Pakistan | Dry-run in the MVP stub; roadmap: local aggregator / short code with two-way support | Real SMS needs procurement and sender registration | Team / PDMA |
| 2 | Should `NOT_RECEIVED` open a field task? | Yes (roadmap build, reason `NOT_RECEIVED`) | No = disputes handled only on the DC's list | PDMA officer |
| 3 | Add a `FIELD_AGENT` user role? | Roadmap; MVP uses DC/officer | New role touches foundation RBAC and NFR6 tests | Foundation owner |
| 4 | SMS for Watch tier alerts? | No; only Warning and Action (roadmap) | Cost per SMS; Watch is low urgency | PDMA officer |
| 5 | Turn the flag on for the demo? | Off for the 3-minute demo; on in a separate rehearsal to show the chain | On adds SMS/task rows to the replay screens | Frontend & pitch |
| 6 | Field-task SLA / escalation if a task stays open | None in the MVP; roadmap: notify the DC after 24 h open | One more timer | DC / PDMA |
| 7 | Proposed `FieldAgentTask` fields (`scenario_id`, `reason_code`, `outcome`, links, completion fields) | Yes (plan.md) | Without `scenario_id`, replay tasks leak into live lists | Foundation owner |
