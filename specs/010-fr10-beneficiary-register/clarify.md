# Clarifications: FR10 Beneficiary Register

## Clarifications

### Session 2026-11-10

- Q: What counts as a valid phone? → A: Pakistani **mobile** only. Strip spaces, dashes, brackets; map `03XXXXXXXXX`, `3XXXXXXXXX`, `923XXXXXXXXX`, `00923XXXXXXXXX` and `+923XXXXXXXXX` to `+923XXXXXXXXX`; then require `^\+923[0-9]{9}$`. Landlines (`+9221…`) and foreign numbers are rejected with `PHONE_INVALID`. WhatsApp needs a mobile, and the guide fixes E.164 `+923XXXXXXXXX`.
- Q: Is the phone required? → A: Yes for every beneficiary. UC10's precondition is a phone on record, and payment notices and confirmations depend on it. A beneficiary with no phone can be added in the roadmap together with the FR16 field-agent path.
- Q: Is CNIC required? → A: Optional (data-model says nullable). When present it is normalised to `XXXXX-XXXXXXX-X` and its hash (`cnic_hash`) is used to block duplicates of the same type.
- Q: How do we de-duplicate? → A: `register_ref` is the upsert key (unique). `cnic_hash` + `type` is unique when CNIC is present. `phone_hash` is **not** unique, because families share phones.
- Q: Is CSV import all-or-nothing? → A: Default `on_error=reject_file`: any invalid row means nothing is saved (same spirit as UC01-5a). `on_error=skip_rows` saves valid rows and reports the rest. `dry_run=true` validates only. Simple to audit and to reason about.
- Q: What is the CSV format? → A: UTF-8 (BOM allowed), comma-separated, header row required, column names exactly as in plan.md §CSV format. Unknown columns are rejected so a typo cannot silently drop data.
- Q: How is `flood_exposure` derived? → A: `flood_exposure = round(0.40·E + 0.35·R + 0.25·H, 3)` where E = low ground (1 − relative elevation within the district), R = river proximity (1 − min(distance_km/10, 1)), H = flood history (min(floods_last_10y/3, 1), else max tehsil `flooded_pct`/30%, else 0.5). Missing components take 0.5 and are listed in `exposure_inputs.imputed`. The weights are settings (`EXPOSURE_WEIGHTS`). This uses data we already have (SRTM tehsil elevation, precomputed Sentinel-1 maps) and stays explainable (Principle IV).
- Q: Can a CSV supply `flood_exposure` directly? → A: No. Exposure is always derived so the method is the same for everyone. The CSV supplies the inputs (`elevation_m`, `river_distance_km`, `floods_last_10y`).
- Q: Delete or deactivate? → A: Deactivate (`is_active=false`, with reason). Allocations, payments and audit entries reference beneficiaries, so hard delete would break history.
- Q: Who can write? → A: `PDMA_OFFICER` and `ADMIN`. Who can read? Officer, admin, and the DC of the beneficiary's district. Donors: 403 on every route. A DC asking for another district's beneficiary gets 404 so existence is not leaked.
- Q: Do list responses include PII? → A: Name is shown in full to officer and DC (they need it to review a list). Phone, CNIC and wallet id are always masked (`+92300*****67`, `*****-*******-3`, `*******4567`). The officer can reveal one record through `POST /beneficiaries/{id}/reveal`, which is audited as `PII_REVEALED`.
- Q: What distributions does the synthetic generator use? → A: Configured in `data/seeds/synthetic_register.yaml` with sourced approximations, labelled "approximate, verify before external use": mean household size Sindh 5.9, KP 7.4 (PBS census tables, rounded); poverty score Beta with mean `0.30 + 0.45 × district.vulnerability_weight`; no-wallet share Sindh 25%, KP 20%; wallet split JazzCash 55% / Easypaisa 45%; existing support (BISP-style) for 35% of Sindh and 25% of KP households, PKR 40 000–56 000 a year; one shopkeeper per 25 households; stock value log-normal (median PKR 120 000 Sindh, 150 000 KP); river distance exponential (mean 6 km Sindh plain, 3 km KP valleys); floods in the last 10 years Poisson (mean 1.5 Sindh, 1.0 KP). The exact values are config, so the team can tune them without code.
- Q: How big is the synthetic register? → A: `sample_rate` × estimated households per tehsil (population / mean household size). Default `sample_rate=0.01` gives roughly 2 000–3 500 households for Dadu, plus shopkeepers. FR5 and FR6 tune the sample rate and fund size together so the demo shows some `UNMET_NEED` at Action.
- Q: Is generation deterministic? → A: Yes. `numpy.random.default_rng(seed)` per tehsil (seed derived from `seed` + tehsil pcode), ids are `uuid5(NAMESPACE, f"{seed}:{register_ref}")`, and `register_ref = SYN-<tehsil_pcode>-<H|S>-<00001>`. Re-running upserts identical rows.
- Q: Synthetic phone numbers might belong to real people. → A: The generator uses numbers in `+92300000xxxx`-style ranges but cannot guarantee they are unused. So FR14 MUST NOT message any `simulated=true` beneficiary unless the phone is in `DEMO_PHONE_ALLOWLIST`. Demo personas (team members) come from `data/seeds/demo_personas.yaml`, which is git-ignored; only the `.example` file is committed.
- Q: Who sets `phone_valid=false`? → A: FR14 when a send fails with an invalid-number error (UC10-1a). FR10 resets it to true when the phone is changed, and lets the officer filter on it.
- Q: How are wallet ids validated? → A: JazzCash and Easypaisa mobile wallets use a mobile number; we normalise like a phone and store it as `03XXXXXXXXX`. The generator makes about 2% of wallet ids end in `000` so FR17's deterministic `INVALID_WALLET` failure appears in the demo (config `invalid_wallet_rate`).
- Q: How is coverage measured for the fairness check? → A: `estimated_households = population / mean_household_size(province)`; `coverage = registered active households / estimated_households`; `relative_coverage = coverage / median coverage of the district's tehsils`. Relative coverage is used because the synthetic register is a sample. `completeness` = share of active beneficiaries whose ranking inputs are not imputed (households: `poverty_score`, `family_size`, exposure; shopkeepers: `stock_value_pkr`, exposure).
- Q: Concurrent imports? → A: Serialise per district with `pg_advisory_xact_lock(hash('register:'||district_id))`.

## Resolved by constitution decisions

- **D5**: `Beneficiary` is defined in the canonical data model; FR10 only proposes additions.
- Principles **III** (audit), **VI** (encryption, roles, masking), **VIII** (simulated label), **IX** (distributions and weights are config) apply directly. D1–D4 and D6 do not apply.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Should `phone_hash` be a keyed HMAC instead of plain SHA-256? The Pakistani mobile space (~10⁹ numbers) is easy to brute-force. | Use the foundation's `phone_hash` as is; recommend the foundation switch to HMAC-SHA-256 with `PHONE_HASH_KEY` | Re-hash all rows on change (one migration); FR15 lookups unaffected if they use the same function | Foundation owner |
| 2 | Allow beneficiaries without a phone (reachable only by field agent)? | No, phone required | Would need FR16 field-agent path and nullable `phone_hash` handling in FR14/FR15 | Product (PDMA officer persona) |
| 3 | Mean household sizes and other distribution values: confirm against PBS 2023 census district tables | Sindh 5.9, KP 7.4 and the other values above, labelled approximate | Changes synthetic register size and ranking spread; config only | Data & ML |
| 4 | Should the register be per scenario (replay copy) rather than shared reference data? Data-model gives `Beneficiary` no `scenario_id`. | Shared reference data; replay allocations and payments carry `scenario_id` | A replay that flags `phone_valid=false` would affect live data; FR14 should skip that write in replays | Foundation owner + FR5 |
| 5 | Should beneficiaries without a wallet (`NONE`) be eligible for ranking? | Yes, eligible; FR17 fails them with `NO_WALLET` and the DC can cancel to return money | Excluding them would bias against the unbanked poor (NFR5) but saves pool money | PDMA officer (FR11 owner implements) |
| 6 | Import mode default | `reject_file` (all-or-nothing) | `skip_rows` default is friendlier but can hide partial loads | PDMA officer |
| 7 | Exposure weights 0.40 / 0.35 / 0.25 | As stated, in settings | Changes ranking; config only, audited via `EXPOSURE_RECOMPUTED` | Data & ML |
