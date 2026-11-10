# Clarifications: 000 Foundation

## Clarifications

### Session 2026-11-10

- Q: Which backend stack? → A: Python 3.12 + FastAPI + PostgreSQL/PostGIS, chosen by the user. Earth Engine and the ML work are Python-native, and spec-kit is configured for Python scripts.
- Q: Should the 6 contradiction recommendations be applied? → A: Yes, all 6, chosen by the user. They are recorded as constitution decisions D1–D6.
- Q: Microservices or one service? → A: One FastAPI service with in-process APScheduler (Principle X). A 32-hour build doesn't justify a broker.
- Q: How are replays isolated? → A: A `scenario_id` column on every operational table, plus a context variable. This was chosen over separate schemas or databases because it's simpler and lets the replay call the exact live code path.
- Q: How is PII encrypted? → A: Fernet column encryption with the key from env, plus a SHA-256 `phone_hash` for lookups. Postgres `pgcrypto` was rejected because the key would sit in SQL statements.
- Q: Auth method? → A: OAuth2 password flow with JWT access tokens (60 min) and bcrypt hashes. No refresh tokens in the MVP; users sign in again.
- Q: Where do boundaries come from? → A: OCHA COD-AB Pakistan adm2/adm3 (document Q11: 160 districts, 577 tehsils). Only Sindh and KP are seeded for the pilot.
- Q: Money type? → A: `NUMERIC(14,2)` PKR in the database, `Decimal` in Python, a string in JSON.
- Q: Time zone? → A: Store UTC, display Asia/Karachi. The daily pipeline runs at 06:00 PKT (01:00 UTC), after the overnight IMERG Early run is available.

## Resolved by constitution decisions

- **D5**: data-model additions (`Payment`, `Confirmation`, `Approval`, `User`, `TriggerEvent`, `Allocation`).
- **D6**: no `PDMA_DG` role in the MVP.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Where will the demo be hosted? | Render or Railway with managed Postgres (PostGIS enabled), plus a laptop docker-compose as backup | Deployment scripts only | Backend & alerts |
| 2 | Which districts are seeded as active? | Sindh: Dadu, Larkana, Qambar Shahdadkot, Jacobabad, Khairpur, Sanghar, Mirpurkhas. KP: Buner, Swat, Shangla, Charsadda, Nowshera. Everything else is seeded but inactive | Forecast run time and the size of the demo map | Data & ML + pitch |
| 3 | JWT lifetime | 60 min | Re-login frequency during the demo | Backend & alerts |
| 4 | Is a beneficiary self-service login needed? | No. Beneficiaries use WhatsApp only | Adds a role and screens | Team |
