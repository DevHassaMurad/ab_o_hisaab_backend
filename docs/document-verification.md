# Grok document extraction update

Documents are sent to xAI's vision API using server-only XAI_API_KEY and configurable XAI_DOCUMENT_MODEL. Responses are bounded, schema validated, and never treated as authenticity evidence. Do not put the key in frontend variables. No model training is performed.

NGOs must supply two distinct capability-protected processes: verification_id/token for the selected CNIC/passport and ngo_verification_id/token for ngo_registration. Both must complete checks or enter manual review, and both are consumed atomically with registration.

Set DOCUMENT_PASSPORT_MODE=extraction during dummy-document testing. This permits missing/invalid MRZ digits but still requires readable passport number and identity fields. It never automatically approves passports, even when auto-approval is enabled. Switch to strict to restore MRZ enforcement. Extraction success does not prove authenticity. xAI receives document images; disclose this processing to users.

# Document verification and connection repair

## Connection diagnosis

The process at port 5800 was serving old code: OpenAPI exposed only
`/api/auth/authenticate` and `/api/auth/protected`. New registration, document and
health routes returned 404. The Vite catch-all also sent `/api/health` to the
separate port-8000 forecasting backend. Restarting the current MySQL auth app and
proxying auth, documents, admin document review and health to port 5800 repaired
the connection. Database `SELECT 1`, direct health and proxied health returned 200;
protected document routes correctly returned 401 without credentials.

The auth and forecasting apps still have separate models, databases and Alembic
histories. This implementation modifies `main:app` / `migrations/` only. Explicit
CORS origins support direct calls; same-origin Vite requests use the proxy.
Use the configured backend virtual environment. During development use:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 5800 --reload
```

The frontend defaults and `.env.example` use `http://127.0.0.1:5800`; adjust
`VITE_AUTH_BACKEND_URL` and `CORS_ORIGINS` for another host. In deployment route
`/api/auth`, `/api/documents`, `/api/admin/documents` and `/api/health` to the auth
app (including `/api/v1` aliases when used). Do not proxy document traffic to the
forecasting application. Serve client and API over HTTPS.

## Registration workflow

The form collects account details, then one document selected from CNIC, Passport,
Home Address Proof or NGO Registration Certificate. NGO certificates are available
for NGO registrations. Every role still has its own existing profile table.

Before creating a user, an upload creates a `verification_processes` row and returns
a random capability token. Only its SHA-256 digest is stored. No password or name
is saved in this process. The token remains in browser memory, is sent in a header,
and expires with the process after 24 hours. Do not put it in a URL or log it.

`POST /documents/verify` increments the persisted attempt counter under a row lock,
then queues a real background task. The UI polls committed stages; no animation
timer invents completed work. Replacing an upload retains process ID, token and
attempt count. A fourth attempt is refused by the server. Three unsuccessful
attempts enable a persisted manual review request; failed OCR does not reject the
whole registration. Successful local content checks also allow registration, with
Pending status and administrator review under the default policy.

Registration is now JSON with the existing snake_case profile fields plus
`verification_id` and `verification_token`. It consumes the process once, in the
same transaction as user/profile/document creation. Owner type, identity type,
readable identity number, attempt outcome and review request are checked server-side.
Arbitrary URLs, client-provided results and legacy profile-less requests cannot
bypass this gate. Duplicate email and transaction failures leave the verification
process available for an appropriate retry. No files are deleted on a failed claim.

`users.registration_status` is separate from document status: `under_review`,
`approved` or `corrections_required`. Existing users retain null for compatibility.
New accounts still require email confirmation and administrator-assigned portal
permissions; approving a document does not assign a privileged role.

## Processing architecture

All image processing, OCR, content rules, classification, encryption and optional
authenticity-service integration are in `agents/`:

* `document_preprocessor.py`: Pillow format/decode checks; bounded PDFium rendering;
  EXIF rotation, quadrilateral boundary detection, perspective correction, line
  deskew, blur/brightness/contrast measurement and CLAHE/Otsu enhancement.
* `ocr_service.py`: real Tesseract LSTM OCR, orientation detection and configurable
  language/timeout. Raw recognized text remains transient and is never logged.
* `document_classifier.py`: explainable keyword evidence. It is not a trained model.
* `document_validator.py`: CNIC format/name fields, passport identity and TD3 MRZ
  check digits, address-proof content, and NGO organization/registration fields.
* `document_verification_agent.py`: composes actual stages, validates configured
  authenticity-service evidence and encrypts only necessary extracted fields.

Local checks do **not** authenticate official documents. Default
`DOCUMENT_AUTO_APPROVE=false` keeps status Pending. Setting it true is insufficient
on its own: automatic approval also requires successful local checks and explicit
positive evidence from an operator-configured trusted HTTPS authenticity service.
Its response schema is `authenticity_verified` (strict boolean), `provider` and
`reference`. Timeouts, malformed responses or absent configuration cannot approve.
No remote service is called unless `DOCUMENT_AUTHENTICITY_URL` is configured.

No custom authenticity model has been trained or evaluated. Before relying on
automated approval, validate the provider and approval policy, acquire representative
labeled consented documents, and measure extraction accuracy, false approvals,
false rejections and performance across languages, layouts and capture quality.
CNIC Urdu and regional layouts need appropriate Tesseract language data and testing.

## API (under `/api` and `/api/v1`)

| Method and path | Purpose / authorization |
| --- | --- |
| `GET /documents/config` | Public size limits and supported types |
| `POST /documents/upload` | Multipart `file`, `user_type`, `document_type`; optional `process_id` and capability header for replacement |
| `POST /documents/verify` | JSON `document_id` (process UUID), capability header; 202 queues a real attempt |
| `GET /documents/{id}/verification-status` | Capability-protected process stage, completed stages, results and attempts |
| `GET /documents/{id}/preview` | Capability-protected raster PNG; never serves an active uploaded PDF |
| `POST /documents/{id}/request-manual-review` | Capability-protected persisted manual review request |
| `POST /auth/register` | JSON account/profile + verification capability; creates one shared document row |
| `GET /documents/me` | Existing authenticated user's documents |
| `GET /documents` | Existing administrator review queue |
| `GET /documents/{id}/download` | Existing owner/admin attachment download (numeric document ID) |
| `PATCH /admin/documents/{id}/review` | Authenticated administrator; JSON `status`, optional `reason`; no self-review |
| `POST /admin/documents/{id}/notify` | Administrator retry of an undelivered completed-review email |

Verification process IDs are UUIDs before registration; registered document IDs
are existing integer IDs. Send the returned token in `X-Verification-Token`, never
in query parameters. The existing authenticated upload contract (`document_slot`
plus file) and legacy admin review path remain for post-registration operations.
The previous profile-less `/auth/register` contract intentionally no longer bypasses
verification; existing sign-in, email confirmation and reset contracts are unchanged.

## Security, persistence and deployment

Limits are configurable in the environment examples. Defaults: 10 MiB per file,
64 MiB request, 24 megapixels per image, three PDF pages, two workers and five new
processes per client IP per hour. The three-attempt ceiling is enforced in both
controller and DB constraints. IP keys are HMACs, not raw stored addresses. Behind
a reverse proxy, configure trusted forwarded headers; do not trust arbitrary
client-supplied addresses. Existing per-process request throttling also applies.

Both MIME/signature and actual decoders validate files. Malformed/oversized images,
encrypted/damaged PDFs, scripts and embedded PDF attachments are rejected. PDFium
calls are protected by a mutex because PDFium is not thread safe. The preview API
returns raster images with no-store/nosniff headers. Keep decoder libraries patched;
for internet-scale untrusted processing, move decoding/OCR into resource-limited
isolated worker containers and add a malware scanner.

Extracted fields are encrypted with Fernet. Configure `DOCUMENT_DATA_KEY`, or keep
the existing `AUTH_CODE_SECRET`/`JWT_SECRET_KEY` stable for domain-separated key
derivation. Back up the key securely; changing it requires re-encrypting prior
extracts. Original documents remain in the existing private service-account-only
storage directory, never static hosting. Browser responses expose checks and field
names, not raw identity/address text. Apply a documented retention policy for
expired unclaimed processes and files; no destructive purge is run automatically.

BackgroundTasks uses a worker thread and fresh DB sessions per job. Every stage
is persisted. On a single-instance server restart, interrupted work becomes a
retryable error without resetting attempt counts. Configure one auth app instance
for this local worker implementation; before scaling to multiple instances, use a
durable external queue with leases and per-job isolation instead of startup recovery.

Completed manual reviews update overall registration state and queue an email via
the existing SMTP mailer. A persisted `review_notification_sent_at` records delivery;
failed delivery remains retryable through the admin notify endpoint. SMTP must be
configured and reachable. Email includes no raw document contents.

## Dependencies and migrations

`requirements.txt` includes OpenCV, NumPy, Pillow, pytesseract, PDFium, cryptography
and httpx. Tesseract itself is an OS installation; set `TESSERACT_CMD` or PATH.
Windows' `C:/Program Files/Tesseract-OCR/tesseract.exe` is auto-detected. Install
language packs needed by `DOCUMENT_OCR_LANG` (default `eng`). No paid service key
is needed for local OCR. The optional trusted service needs its own credentials.

Revision `0005_document_verification` follows `0004_registration_documents`. It
adds the process table, attempt/review/encrypted-result fields and registration
state. Existing profile rows and document categories remain compatible. Downgrade
maps the new NGO category to the legacy equivalent before restoring constraints;
it removes new analysis state, so do not downgrade a populated deployment casually.

Review and back up the intended database, then use the existing
`.\.venv\Scripts\python.exe artisan.py migrate` command. No destructive reset or
seed is required. Tests run migration lifecycles only against disposable SQLite.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_verification tests.test_registration tests.test_auth_flow tests.test_migrations -q
```

Tests cover real OpenCV/Tesseract processing of synthetic fixtures (not official
documents), PDF/PNG/JPEG decoding and raster previews, document-specific rules,
MRZ checks, encrypted identity matching, all four registration profiles, three
attempts/replacement/manual fallback, token ownership/replay/expiry, transactions,
admin/self-review, notification persistence, CORS, existing auth, migration parity
and rollback. Test-only fixtures exercise orchestration; production has no fake
verification results or hardcoded success paths.
