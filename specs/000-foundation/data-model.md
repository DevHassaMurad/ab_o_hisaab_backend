# Canonical Data Model

**Owner**: 000-foundation | **Version**: 1.1 (2026-11-10) | **Constitution**: v1.1.0, decision D5

This is the **single source of truth** for entities, fields and states. FR plans reference it. They must not redefine an entity. If an FR needs a new field, propose it in that FR's plan.md, and the foundation owner merges it here.

**v1.1 changelog**: merged the "Proposed data-model additions" from all 22 FR plans. Each merged field is tagged with the FR that needs it, e.g. *(FR17)*. The main changes:
- `scenario_id` added to every operational table that lacked it.
- `reserved_pkr` now also counts Failed payments.
- Audit chains are now per `chain_key` and hash the whole entry.
- `Alert` → `Confirmation` is now one-to-many.
- `ReplayScenario.current_date` is renamed `sim_date`, because `current_date` is a reserved word in SQL.
- The duplicate purposes `APPROVAL_DECISION` (FR8) and `DECISION_NOTICE` (FR14) are unified as `DECISION_NOTICE`.

Conventions:
- Every table has `id: UUID` (PK), `created_at` and `updated_at`.
- All times are `timestamptz`, stored in UTC.
- Money is stored as `NUMERIC(14,2)` PKR in fields named `*_pkr`.
- PII fields marked 🔒 are Fernet-encrypted at rest.
- Fields marked 🧪 carry the "simulated" label (Principle VIII).
- `scenario_id` (nullable FK → `ReplayScenario`) appears on every operational table. `NULL` means live data; a value means the row belongs to a replay run (FR5). Live queries always filter `scenario_id IS NULL`.
  - **Exception**: `Beneficiary` is a shared register used by both live and replay runs (FR10 decision). Replay isolation applies to what is *done* to beneficiaries (allocations, payments, alerts), not to the register itself.
- Unique keys that contain nullable columns use `UNIQUE NULLS NOT DISTINCT` (PostgreSQL 15+), so re-running a job is idempotent. Written as **NND** below.
- `phone_hash` = SHA-256 of the normalised E.164 phone plus `PHONE_HASH_SALT` (see `000-foundation/implement.md`).

---

## 1. Geography

### District
| Field | Type | Notes |
|---|---|---|
| pcode | str, unique | OCHA COD-AB P-code, e.g. `PK605` |
| name | str | |
| name_ur | str, nullable | Urdu name for messages and explanations *(FR3, FR14)* |
| province | enum `SINDH`, `KP`, `PUNJAB`, `BALOCHISTAN`, `GB`, `AJK`, `ICT` | |
| flood_mode | enum `SLOW_ONSET`, `FLASH` | Sindh → `SLOW_ONSET`, KP → `FLASH` by default (FR4) |
| vulnerability_weight | float 0–1 | From World Bank / EasyData indicators (FR1 refresh) |
| vulnerability_inputs | JSONB, nullable | Raw value, normalised value, imputed flag, period and source per indicator, plus the weights *(FR1, Principle IV, NFR5)* |
| geom | MultiPolygon (PostGIS, SRID 4326) | |
| centroid_lat, centroid_lon | float | Used for NASA POWER point queries |
| is_active | bool | Inactive districts are skipped by the pipeline (NFR11) |

### Tehsil
| Field | Type | Notes |
|---|---|---|
| pcode | str, unique | |
| district_id | FK District | A district contains tehsils |
| name | str | |
| name_ur | str, nullable | *(FR3)* |
| population | int | |
| elevation_mean_m | float | From SRTM |
| slope_mean_deg | float, nullable | From SRTM, used by flash mode *(FR2, FR4)* |
| geom | MultiPolygon | |

---

## 2. Data ingestion (FR1, FR2, FR21)

### ClimateObservation
| Field | Type | Notes |
|---|---|---|
| district_id | FK, nullable | Set for NASA POWER. IMERG rows set **both** district and tehsil *(FR2)* |
| tehsil_id | FK, nullable | Set for IMERG (tehsil mean) |
| obs_date | date | |
| source | enum `NASA_POWER`, `GPM_IMERG`, `FORECAST_RAIN` | |
| rainfall_mm | float | Daily total |
| rainfall_max_3h_mm | float, nullable | IMERG, and also forecast rain for flash mode *(FR3)* |
| temp_c | float, nullable | |
| soil_wetness | float 0–1, nullable | NASA POWER `GWETROOT` |
| soil_wetness_top | float 0–1, nullable | NASA POWER `GWETTOP` *(FR1, FR3, FR4)* |
| lead_day | int, nullable | Only for `FORECAST_RAIN` (1–7) |
| provenance | enum `LIVE_FORECAST`, `ARCHIVED_FORECAST`, `ARCHIVED_SHORT_LEAD_PROXY`, `PERFECT_FORECAST_PROXY`, nullable | Only for `FORECAST_RAIN` (Principle VIII) *(FR3)* |
| forecast_model | str, nullable | e.g. `open-meteo:best_match` *(FR3)* |
| quality_flags | JSONB, default `[]` | Validation fixes, e.g. `gwetroot_pct_to_fraction`, `imerg_partial:44/48` *(FR1, FR2)* |
| is_stale | bool | True when copied forward from the last valid data (NFR9) |
| stale_since | date, nullable | Date of the last valid data |
| scenario_id | FK, nullable | |
| *unique NND* | (district_id, tehsil_id, obs_date, source, lead_day, scenario_id) | Re-ingesting never duplicates rows |

### ContextIndicator
Monthly or yearly indicators from World Bank and EasyData.

| Field | Type | Notes |
|---|---|---|
| district_id | FK | |
| indicator | str | e.g. `poverty_headcount`, `housing_kutcha_pct` |
| value | float | |
| period | str | e.g. `2023` |
| source | str | |
| *unique* | (district_id, indicator, period, source) | *(FR1)* |

### DataSourceStatus
| Field | Type | Notes |
|---|---|---|
| source | enum (as above + `SENTINEL1`, `WORLD_BANK`, `EASYDATA`) | unique |
| last_success_at | timestamptz | |
| last_valid_date | date | |
| last_error | text, nullable | |
| is_healthy | bool | |

### SatelliteFloodMap
| Field | Type | Notes |
|---|---|---|
| district_id | FK District | Required *(FR21)* |
| tehsil_id | FK, nullable | NULL means a district-level row *(FR21)* |
| event_id | FK TriggerEvent, nullable | Roadmap link to the event being verified *(FR21)* |
| image_date | date | The "after" image date |
| before_image_date | date, nullable | *(FR21)* |
| sensor | str | `SENTINEL1_GRD` |
| status | enum `OK`, `NO_IMAGERY` | "No imagery" is not the same as zero flooding *(FR21)* |
| flooded_area_km2 | float | New water after removing the JRC permanent-water baseline |
| flooded_pct | float | Share of the tehsil's area |
| before_tile_url, after_tile_url, flood_mask_url | str | Earth Engine thumbnails or static files under `data/replay/` |
| bbox | JSONB `[west, south, east, north]` | EPSG:4326 overlay placement *(FR21)* |
| params | JSONB | Algorithm version, thresholds, acquisition dates, orbit (Principle IV) *(FR21)* |
| precomputed | bool | True for replay events (D1). The UI shows a "precomputed" label |
| simulated | bool 🧪, default false | Real satellite data, so false (D1) *(FR21)* |
| scenario_id | FK, nullable | |
| *unique NND* | (district_id, tehsil_id, image_date, sensor, scenario_id) | |

---

## 3. Forecasting (FR3, FR4, FR5, FR13)

### FloodForecast
| Field | Type | Notes |
|---|---|---|
| district_id | FK | |
| tehsil_id | FK, nullable | Null means district-level aggregate |
| run_date | date | Date the forecast was made |
| lead_day | int 1–7 | |
| target_date | date | `run_date + lead_day` |
| probability | float 0–1 | |
| mode | enum `SLOW_ONSET`, `FLASH` | |
| model_version | str | e.g. `slow-lgbm-2026.11.1` or `rules-fallback-1` |
| features | JSONB | Exact feature values used (Principle IV) |
| forecast_provenance | enum (as ClimateObservation.provenance) | *(FR3)* |
| is_fallback | bool | Rule-based fallback was used (UC04-3a) |
| is_stale_input | bool | Any input was stale |
| scenario_id | FK, nullable | |
| *unique NND* | (district_id, tehsil_id, run_date, lead_day, scenario_id) | *(FR3)* |

### RiskIndex
One row per district (and per tehsil) per run date. It holds the **max over lead days 1–7** and is the value that drives tiers.

The district score is the max over the tehsils that hold at least 10% of the district's population (FR3 default).

| Field | Type | Notes |
|---|---|---|
| district_id, tehsil_id | FK | tehsil nullable |
| run_date | date | |
| score | int 0–100 | `round(100 × calibrated max probability)` |
| peak_lead_day | int | Lead day that produced the max |
| tier | enum `NONE`, `WATCH`, `WARNING`, `ACTION` | Uses the district's `TriggerTier` thresholds |
| mode | enum | *(FR3, Principle IV)* |
| model_version | str | *(FR3, Principle IV)* |
| forecast_provenance | enum | Worst over inputs: proxy > short-lead proxy > archived > live *(FR3)* |
| explanation_en, explanation_ur | text | Template output (D2) |
| top_factors | JSONB | e.g. `[{"factor":"rain_3d_mm","value":142,"contribution":0.38}]` |
| is_fallback, is_stale_input | bool | Propagated |
| stale_since | date, nullable | Date for the stale banner (UC05-1a) *(FR3)* |
| explanation_llm_en, explanation_llm_ur | text, nullable | Roadmap *(FR13)* |
| explanation_llm_status_en, explanation_llm_status_ur | enum `NONE`, `ACCEPTED`, `REJECTED`, `ERROR`, `DISABLED` | *(FR13)* |
| explanation_llm_violations | JSONB, nullable | `{"en":[…],"ur":[…]}` *(FR13)* |
| explanation_llm_model_en, explanation_llm_model_ur, explanation_llm_prompt_version | str, nullable | *(FR13)* |
| explanation_llm_input_hash | char(64), nullable | Skips unchanged inputs *(FR13)* |
| explanation_llm_at | timestamptz, nullable | *(FR13)* |
| scenario_id | FK, nullable | |
| *unique NND* | (district_id, tehsil_id, run_date, scenario_id) | *(FR3)* |

---

## 4. Fund configuration and triggers (FR6–FR9)

### DisasterFund
One fund per district per scenario. FR5 clones the live fund when a replay starts.

| Field | Type | Notes |
|---|---|---|
| district_id | FK | *unique NND (district_id, scenario_id)* |
| allocated_pkr | numeric | Configured total |
| reserved_pkr | numeric | Sum of `Payment` rows in **Reserved, Processing or Failed**. Failed money is held for a retry until cancelled *(FR17, FR18)* |
| disbursed_pkr | numeric | Sum of Paid |
| remaining_pkr | numeric | `allocated − reserved − disbursed`. Kept in sync and checked by a constraint test |
| household_share_pct | int | Default 70 (FR9) |
| pool_spillover_enabled | bool, default true | Unused pool money moves to the other type within the same release. It never moves into a type with a 0% share *(FR9)* |
| household_amount_by_tier | JSONB | e.g. `{"WATCH":3000,"WARNING":7000,"ACTION":15000}` PKR (FR12) |
| shopkeeper_cap_pkr | numeric | Default 50 000 (FR12) |
| shopkeeper_stock_pct | int | Share of stock at risk paid before the cap. Default 40 |
| ranking_weights | JSONB, nullable | Per-district override of the default ranking weights *(FR11)* |
| event_close_days | int | Default 14 (D4) |
| version | int | Optimistic locking |
| scenario_id | FK, nullable | *(FR5)* |

### TriggerTier
Three rows per fund.

| Field | Type | Notes |
|---|---|---|
| fund_id | FK | |
| name | enum `WATCH`, `WARNING`, `ACTION` | |
| min_score | int | Defaults 40, 60, 80. Must strictly increase |
| release_pct | int | Defaults 10, 30, 60. **Increments** that must sum to 100 (D4) |
| requires_approval | bool | True only for `ACTION` (FR8). Locked: not editable via the API |

### TriggerEvent
One flood episode per district (D4).

| Field | Type | Notes |
|---|---|---|
| district_id | FK | |
| opened_at | timestamptz | First day the score is ≥ Watch |
| closed_at | timestamptz, nullable | After `event_close_days` consecutive days below Watch |
| current_tier | enum `NONE`, `WATCH`, `WARNING`, `ACTION`, default `NONE` | Detects tier changes *(FR7)* |
| highest_tier | enum | |
| last_evaluated_date | date, nullable | Same-day re-runs change nothing; out-of-order dates are ignored *(FR7)* |
| below_watch_streak | int, default 0 | Counts days for the D4 close rule *(FR7)* |
| status | enum `OPEN`, `CLOSED` | At most one `OPEN` per district per scenario (partial unique index) |
| confirmation_status | enum `PENDING`, `CONFIRMED`, `NOT_CONFIRMED` | FR21 / UC02 |
| confirmation_checked_at | timestamptz, nullable | *(FR21, roadmap)* |
| confirmation_review_required | bool, default false | Flagged for PDMA review (UC02-4a) *(FR21)* |
| confirmation_detail | JSONB, nullable | *(FR21)* |
| scenario_id | FK, nullable | |

### FundRelease
The district-level tranche for one tier within one event.

| Field | Type | Notes |
|---|---|---|
| event_id | FK TriggerEvent | |
| fund_id | FK | |
| tier | enum | *unique (event_id, tier)*. Each tier fires once per event |
| amount_pkr | numeric | `allocated_pkr × release_pct / 100`, capped by what remains |
| amount_calc | JSONB | `{cumulative_pct, target_pkr, prior_released_pkr, uncapped_pkr, available_pkr, capped, allocated_pkr_at_fire}` *(FR7)* |
| household_pool_pkr, shopkeeper_pool_pkr | numeric | Split by `household_share_pct` |
| split_details | JSONB | Initial pools, rounding, moves between pools, used, unmet, returned to fund (shape in FR9 plan) *(FR9)* |
| purpose | str | Watch: "Preposition boats and food". Warning: "Evacuate, open camps". Action: "Cash support" |
| risk_index_id | FK | The score that fired it |
| status | enum `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `ALLOCATING`, `RELEASED`, `PARTIALLY_RELEASED` | FR17 sets `RELEASED` or `PARTIALLY_RELEASED` |
| allocation_saved_at | timestamptz, nullable | Null means the list is still a draft. Payments can be reserved only once it is set *(FR11)* |
| allocation_saved_by | str, nullable | A user id, `system` or `replay-sim` *(FR11)* |
| pipeline_error | text, nullable | Last downstream failure, cleared on success *(FR7)* |
| released_at | timestamptz, nullable | |
| simulated | bool 🧪 | |
| scenario_id | FK, nullable | |

### Approval
For the Action tier only (FR8, D3).

| Field | Type | Notes |
|---|---|---|
| fund_release_id | FK, unique | |
| requested_at | timestamptz | |
| approver_id | FK User (DC), nullable | |
| decision | enum `PENDING`, `APPROVED`, `REJECTED`, `ESCALATED` | |
| reason | text, nullable | Required for `REJECTED` |
| channel | enum `DASHBOARD`, `WHATSAPP`, `REPLAY_SIM`, nullable until decided | *(FR8)* |
| otp_hash | str, nullable | bcrypt hash of a 6-digit dashboard code, issued on demand |
| otp_expires_at | timestamptz, nullable | 10 min |
| otp_attempts | int, default 0 | Locks after 5 *(FR8)* |
| otp_last_sent_at | timestamptz, nullable | 60 s resend cooldown *(FR8)* |
| wa_code_hash | str, nullable | The code for the WhatsApp `APPROVE <code>` reply (D3) *(FR8)* |
| wa_code_expires_at | timestamptz, nullable | 24 h *(FR8)* |
| wa_code_attempts | int, default 0 | Locks after 5 *(FR8)* |
| decided_at | timestamptz, nullable | |
| decision_message_id | str, nullable, unique | WhatsApp message id of the deciding reply, for idempotency *(FR8)* |
| escalated_at | timestamptz, nullable | Set after 2 h without a decision (D6) |
| escalation_reason | enum `NO_RESPONSE_2H`, `NO_DC_ASSIGNED`, nullable | *(FR8)* |
| simulated | bool 🧪 | True for `REPLAY_SIM` |
| scenario_id | FK, nullable | *(FR8, FR5)* |

---

## 5. Beneficiaries and allocation (FR10–FR12, FR22)

### Beneficiary
Shared register: no `scenario_id` (see Conventions).

| Field | Type | Notes |
|---|---|---|
| register_ref | str, unique | Source register id, used as the upsert key. Synthetic rows use `SYN-<tehsil_pcode>-<H\|S>-<nnnnn>` *(FR10)* |
| type | enum `HOUSEHOLD`, `SHOPKEEPER` | |
| tehsil_id | FK | |
| name 🔒 | str | |
| phone 🔒 | str | E.164 `+92…` |
| phone_hash | str, indexed | For webhook lookup |
| phone_valid | bool | Set false by UC10-1a |
| phone_flagged_at | timestamptz, nullable | *(FR14)* |
| phone_flag_reason | str, nullable | e.g. `TWILIO_21211` *(FR14)* |
| cnic 🔒 | str, nullable | |
| cnic_hash | str, nullable | Partial unique `(type, cnic_hash) WHERE cnic_hash IS NOT NULL` *(FR10)* |
| wallet_provider | enum `JAZZCASH`, `EASYPAISA`, `NONE` | |
| wallet_id 🔒 | str, nullable | |
| lat, lon | float, nullable | |
| preferred_language | enum `UR`, `EN` | Default `UR` |
| — household fields — | | |
| poverty_score | float 0–1, nullable | e.g. a PMT/BISP-style proxy |
| family_size | int, nullable | |
| — shopkeeper fields — | | |
| stock_value_pkr | numeric, nullable | |
| shop_type | str, nullable | |
| — shared — | | |
| flood_exposure | float 0–1 | Derived at import |
| exposure_inputs | JSONB | How exposure was derived, including which inputs were imputed *(FR10)* |
| existing_support_pkr | numeric | Support received in the last 12 months (BISP etc.) |
| is_active | bool, default true | Inactive rows are excluded from ranking *(FR10)* |
| deactivated_at | timestamptz, nullable | *(FR10)* |
| deactivated_reason | text, nullable | Required when deactivating *(FR10)* |
| simulated | bool 🧪 | The MVP register is synthetic. Only allowlisted numbers are ever messaged |

Indexes: `(tehsil_id, type, is_active)`, `phone_hash`, `simulated`.

### Allocation
One row per beneficiary per FundRelease: the ranked list (UC06).

| Field | Type | Notes |
|---|---|---|
| fund_release_id | FK | *unique (fund_release_id, beneficiary_id)* |
| beneficiary_id | FK | |
| rank | int | 1 = highest priority within its type |
| priority_score | float 0–100 | |
| score_inputs | JSONB | Rule inputs and weights (Principle IV) |
| computed_amount_pkr | numeric | Rule output |
| final_amount_pkr | numeric | After override |
| override_reason | text, nullable | Required when final ≠ computed |
| overridden_by | FK User, nullable | |
| overridden_at | timestamptz, nullable | *(FR11)* |
| reason_en, reason_ur | text | Plain-language reason for this rank and amount |
| reason_llm_en, reason_llm_ur | text, nullable | *(FR13, roadmap)* |
| reason_llm_status_en, reason_llm_status_ur, reason_llm_model_en, reason_llm_model_ur, reason_llm_input_hash, reason_llm_violations | as on RiskIndex | *(FR13)* |
| status | enum `FUNDED`, `UNMET_NEED`, `EXCLUDED` | `EXCLUDED` means overridden to 0: no payment, and not counted as unmet *(FR12)* |
| scenario_id | FK, nullable | *(FR11)* |

### FairnessCheck
| Field | Type | Notes |
|---|---|---|
| fund_release_id | FK, **unique** | One current check per release *(FR11)* |
| passed | bool | |
| metrics | JSONB | Per-tehsil share vs vulnerability-weighted expected share; sparse-data flags |
| acknowledged_by | FK User, nullable | Required to save when `passed=false` (UC06-7a) |
| acknowledged_at | timestamptz, nullable | *(FR11)* |
| acknowledgement_note | text, nullable | *(FR11)* |
| scenario_id | FK, nullable | *(FR11)* |

### OutcomeReport
Roadmap FR22.

| Field | Type | Notes |
|---|---|---|
| beneficiary_id | FK | |
| event_id | FK | |
| payment_id | FK Payment, nullable | *(FR22)* |
| channel | enum `WHATSAPP`, `FIELD_AGENT` | |
| status | enum `SENT`, `IN_PROGRESS`, `COMPLETED`, `EXPIRED` | *(FR22)* |
| survey_version | str | `outcome-v1` *(FR22)* |
| current_question | int | *(FR22)* |
| invalid_attempts | int | *(FR22)* |
| answers | JSONB | Shape in FR22 plan |
| sent_at, last_message_at | timestamptz | *(FR22)* |
| received_at | timestamptz, **nullable** | Null until completed *(FR22)* |
| last_provider_message_id | str, nullable | *(FR22)* |
| simulated | bool 🧪 | *(FR22)* |
| scenario_id | FK, nullable | *(FR22)* |
| *unique NND* | (beneficiary_id, event_id, scenario_id) | *(FR22)* |

---

## 6. Payments (FR17, FR18)

### Payment
One transfer to one beneficiary.

| Field | Type | Notes |
|---|---|---|
| allocation_id | FK, unique | |
| fund_release_id | FK, indexed | *(FR17)* |
| beneficiary_id | FK | |
| district_id | FK, indexed | Used for DC scoping and counts *(FR17)* |
| amount_pkr | numeric | |
| status | enum `RESERVED`, `PROCESSING`, `PAID`, `FAILED`, `CANCELLED` | |
| failure_reason | enum `INVALID_WALLET`, `LIMIT_REACHED`, `PROVIDER_TIMEOUT`, `PROVIDER_ERROR`, `NO_WALLET`, null | |
| failure_detail | text, nullable | |
| retry_count | int | Max 3 |
| idempotency_key | str, unique | `{allocation_id}:{retry_count}` |
| provider | enum `SIMULATED_JAZZCASH`, `SIMULATED_EASYPAISA`, **nullable** | Null when the wallet provider is `NONE` *(FR17)* |
| provider_txn_id | str, nullable | |
| processing_since | timestamptz, nullable | Used for the stuck flag (> 24 h, UC09-1a) |
| is_stuck | bool | |
| paid_at, cancelled_at | timestamptz, nullable | |
| simulated | bool 🧪 | Always true in the MVP |
| scenario_id | FK, nullable | |

Indexes: `(scenario_id, district_id, status)`, and a partial index `WHERE status = 'PROCESSING'` *(FR17)*.

Reservation timing: Watch and Warning payments are reserved when their allocation is saved. Action payments are reserved only after DC approval (constitution D3).

**State machine** (anything not listed is rejected with 409; the full table is in the FR17 plan):

```
RESERVED ──send──▶ PROCESSING ──ok──▶ PAID (terminal)
    │                  │
    │                  └──fail──▶ FAILED ──retry (≤3)──▶ PROCESSING
    │                                 └──cancel──▶ CANCELLED (terminal, money back to the fund)
    └──cancel──▶ CANCELLED
```

### PaymentEvent
The payment timeline (UC09-3).

| Field | Type | Notes |
|---|---|---|
| payment_id | FK | |
| action | enum `RESERVE`, `SEND`, `RESULT`, `RETRY`, `CANCEL`, `STUCK_FLAG`, `RECONCILE` | *(FR17)* |
| from_status, to_status | enum | |
| idempotency_key | str, nullable | *(FR17)* |
| failure_reason | enum, nullable | *(FR17)* |
| provider_txn_id | str, nullable | *(FR17)* |
| actor | str | `system`, or a user id |
| note | text | Required for retry and cancel (FR18) |
| at | timestamptz | |
| scenario_id | FK, nullable | *(FR17)* |

---

## 7. Communication (FR14–FR16)

### Alert
| Field | Type | Notes |
|---|---|---|
| recipient_type | enum `BENEFICIARY`, `DC`, `PDMA_OFFICER`, `ADMIN` | `ADMIN` for data-source alerts *(FR1)* |
| beneficiary_id / user_id | FK, nullable | |
| district_id | FK, nullable, indexed | *(FR14)* |
| purpose | enum `TIER_ALERT`, `PAYMENT_NOTICE`, `APPROVAL_REQUEST`, `ESCALATION`, `DECISION_NOTICE`, `REPLY_ACK`, `OUTCOME_SURVEY`, `DATA_SOURCE_DOWN`, `VERIFICATION_REVIEW` | *(FR1, FR8, FR14, FR15, FR21)* |
| channel | enum `WHATSAPP`, `SMS`, `FIELD_AGENT` | |
| language | enum `UR`, `EN`, `UR_EN` | Beneficiary messages default to `UR_EN` (Urdu first) |
| template_key | str | |
| content_sid | str, nullable | Approved WhatsApp template used in production *(FR14)* |
| message_text | text | Rendered. Codes and OTPs are masked (FR14 `sensitive_keys`) |
| status | enum `QUEUED`, `SENT`, `DELIVERED`, `FAILED`, `READ` | |
| priority | smallint, default 5 | 1 = officials … 5 = other *(FR14)* |
| dedupe_key | str | *unique NND (dedupe_key, scenario_id)* *(FR14)* |
| expects_reply | bool, default false | True for `PAYMENT_NOTICE` and `OUTCOME_SURVEY` *(FR14)* |
| to_phone_hash | str, nullable, indexed | FR15 matches replies with it *(FR14)* |
| provider_message_id | str, nullable, unique | |
| related_payment_id / fund_release_id / approval_id | FK, nullable | |
| triggered_at | timestamptz, nullable | NFR2 anchor *(FR14)* |
| queued_at, sent_at | timestamptz | `sent_at − triggered_at` ≤ 5 min (NFR2) |
| delivered_at, read_at, failed_at | timestamptz, nullable | *(FR14)* |
| attempt_count | int, default 0 | *(FR14)* |
| next_attempt_at | timestamptz, nullable | Backoff *(FR14)* |
| error_code, error_detail | str / text, nullable | No PII *(FR14)* |
| fallback_of_id | FK Alert, nullable | Chain WhatsApp → SMS → field agent (FR16) |
| resend_of_id | FK Alert, nullable | Manual re-send *(FR14)* |
| note | text, nullable | Operator note, never in audit payloads *(FR14)* |
| simulated | bool 🧪 | Dry-run send or simulated subject *(FR14)* |
| scenario_id | FK, nullable | |

Indexes: queue `(priority, queued_at) WHERE status = 'QUEUED'`; fallback scan `(purpose, channel, sent_at) WHERE expects_reply AND fallback_of_id IS NULL` *(FR14, FR16)*.

### Confirmation
Append-only. **One alert can have many confirmations.** The effective answer is the latest `CONFIRMED` or `NOT_RECEIVED` *(FR15)*.

| Field | Type | Notes |
|---|---|---|
| alert_id | FK | |
| beneficiary_id | FK | |
| payment_id | FK, nullable | |
| reply_raw 🔒 | text | Free text may contain PII *(FR15)* |
| channel | enum `WHATSAPP`, `SMS`, `FIELD_AGENT` | |
| status | enum `CONFIRMED`, `NOT_RECEIVED`, `UNPARSEABLE` | A reply of `1` means confirmed and `2` means not received |
| provider_message_id | str, nullable, unique | *(FR15)* |
| recorded_by | FK User, nullable | Staff-recorded outcome (FR16) *(FR15)* |
| received_at | timestamptz | |
| simulated | bool 🧪 | *(FR15)* |
| scenario_id | FK, nullable | *(FR15)* |

Indexes: `(payment_id, received_at DESC)`, `(alert_id)`.

### FieldAgentTask
Roadmap FR16.

| Field | Type | Notes |
|---|---|---|
| beneficiary_id, payment_id | FK | |
| district_id | FK | *(FR16)* |
| source_alert_id | FK Alert | The root WhatsApp notice *(FR16)* |
| alert_id | FK Alert, nullable | The field-agent hand-off alert *(FR16)* |
| reason | str | English text |
| reason_code | enum `NO_REPLY_12H`, `INVALID_PHONE`, `NOT_RECEIVED` | *(FR16)* |
| status | enum `OPEN`, `DONE` | At most one `OPEN` per payment per scenario |
| due_at | timestamptz, nullable | *(FR16)* |
| outcome | enum `CONFIRMED`, `NOT_RECEIVED`, `UNREACHABLE`, nullable | *(FR16)* |
| note 🔒 | text, nullable | *(FR16)* |
| assigned_to, done_by | FK User, nullable | *(FR16)* |
| done_at | timestamptz, nullable | *(FR16)* |
| simulated | bool 🧪 | *(FR16)* |
| scenario_id | FK, nullable | *(FR16)* |

---

## 8. Audit and users (FR20, NFR6)

### AuditLog
Append-only (Principle III). The normative hash spec is in `020-fr20-audit-log/plan.md`.

| Field | Type | Notes |
|---|---|---|
| chain_key | str, indexed | `live`, or `replay:{scenario_id}:{run_no}` *(FR20)* |
| seq | bigint | *unique (chain_key, seq)*, starting at 1 per chain *(FR20)* |
| event_type | str | e.g. `FUND_SETTINGS_UPDATED`, `TIER_FIRED`, `APPROVAL_DECIDED`, `ALLOCATION_SAVED`, `PAYMENT_STATUS_CHANGED`, `ALERT_SENT`, `RECEIPT_CONFIRMED`, `RECEIPT_NOT_RECEIVED`, `RECEIPT_UNPARSEABLE`, `FIELD_TASK_CREATED`, `FIELD_TASK_DONE`, `FLOOD_VERIFIED` |
| entity_type, entity_id | str, UUID | |
| actor | str | user id or `system` |
| payload | JSONB | Canonical, **no raw PII** (ids and hashes only) |
| timestamp | timestamptz | |
| prev_hash | char(64) | Genesis = 64 zeros |
| hash | char(64) | SHA-256(prev_hash ‖ canonical_json(envelope) ‖ timestamp). The envelope is `{chain_key, seq, event_type, entity_type, entity_id, actor, scenario_id, payload}` |
| scenario_id | FK, nullable, `ON DELETE RESTRICT` | *(FR20)* |

Indexes: `(chain_key, event_type, timestamp DESC)`, `(entity_type, entity_id)`. A DB trigger blocks UPDATE and DELETE.

### User
| Field | Type | Notes |
|---|---|---|
| email | str, unique | |
| password_hash | str | bcrypt |
| full_name | str | |
| role | enum `PDMA_OFFICER`, `DC`, `DONOR`, `ADMIN` | `PDMA_DG` (D6) and `FIELD_AGENT` (FR16) are roadmap |
| district_id | FK, nullable | Required for `DC` |
| phone 🔒 | str, nullable | Used for WhatsApp approvals |
| phone_hash | str, nullable | |
| is_active | bool | |

### DonorContribution
Links a donor to fund(s) (UC11 precondition).

| Field | Type | Notes |
|---|---|---|
| donor_id | FK User | |
| fund_id | FK DisasterFund | |
| amount_pkr | numeric | |
| contributed_at | date | |

---

## 9. Replay (FR5)

### ReplayScenario
| Field | Type | Notes |
|---|---|---|
| key | str, unique | `sindh-2022`, `buner-2025`, `control-2021` |
| name | str | |
| start_date, end_date | date | |
| districts | ARRAY(FK) | |
| expected_outcome | enum `TRIGGER`, `NO_TRIGGER` | For backtests (NFR3) |
| status | enum `IDLE`, `RUNNING`, `AWAITING_APPROVAL`, `COMPLETED`, `FAILED` | *(FR5)* |
| sim_date | date, nullable | The replay clock. Renamed from `current_date` *(FR5)* |
| run_no | int, default 1 | Goes up by one on reset, which starts a fresh audit chain *(FR5, FR20)* |
| config | JSONB | Hero district, onset date, minimum lead days, approval mode, mode snapshot, manifest hash, forecast provenance, funds *(FR5)* |
| started_at, completed_at, last_step_at | timestamptz, nullable | *(FR5)* |
| last_error | text, nullable | *(FR5)* |

---

## Relationship summary

```
District 1─* Tehsil
District 1─* DisasterFund (one per scenario) 1─3 TriggerTier
District 1─* TriggerEvent 1─* FundRelease (≤1 per tier) 1─0..1 Approval
FundRelease 1─* Allocation 1─0..1 Payment 1─* PaymentEvent
FundRelease 1─1 FairnessCheck
Beneficiary 1─* Allocation, Alert, Confirmation, OutcomeReport
Alert 1─* Confirmation ; Alert 0..1─1 Alert (fallback_of, resend_of)
Payment 0..1─1 FieldAgentTask (open)
District 1─* SatelliteFloodMap ; Tehsil 1─* ClimateObservation, SatelliteFloodMap
District 1─* FloodForecast, RiskIndex
Every state change ──▶ AuditLog (same transaction, chain per chain_key)
```
