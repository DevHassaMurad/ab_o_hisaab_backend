# Authentication and account recovery

The existing MySQL `Authentication` controller in `controller/authentication.py`
owns registration, login, code requests, code verification and password changes.
`config/auth_mail.py` handles SMTP and the branded HTML/plain-text email rendering.
No additional authentication controller is used.

## Apply the schema

```powershell
.\.venv\Scripts\python.exe artisan.py migrate
```

Revision `0003_auth_verification` adds `users.email_verified_at`,
`users.token_version` and `auth_challenges`. Existing accounts start unverified
and must confirm their email before their next login. Password hashes, image,
role assignments and other existing user data are retained.

Run the migration before starting the updated backend. A one-step rollback removes
the new verification/session fields and pending challenges; it retains users and roles.

## Mail configuration

For local development, capture emails in Mailpit without sending them externally:

```powershell
docker compose --profile mail up -d mysql mail
.\.venv\Scripts\python.exe main.py
```

Use `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025`, `SMTP_SECURITY=none` and
`SMTP_FROM_EMAIL=accounts@aab.test`. Open <http://127.0.0.1:8025> to view messages.
The `.env` examples include these values. Do not use the `.test` sender for real mail.

For real delivery, configure your provider's SMTP host/username/password and a verified
sender address. Use `SMTP_SECURITY=starttls` (usually port 587) or `ssl` (usually 465).
Keep `AUTH_CODE_SECRET` private and stable; rotating it invalidates outstanding codes
and password-reset grants. It falls back to `JWT_SECRET_KEY` when unset.

The emails use the portal's existing brand colour, restrained typography, a prominent
code, expiration instructions and security context. Every message includes plain text.
Passwords, codes and reset grants are never written to application logs.

## API contracts

The canonical prefix is `/api/auth`. The existing portal path `/api/v1/auth` is
also accepted by the MySQL app as a compatibility alias.

| Method/path after prefix | Input | Result |
| --- | --- | --- |
| `POST /authenticate` | JSON `email`, `password`; optional existing `user_name` | Access token, or verification/role error |
| `POST /token` | Form `username` = email, `password` | Compatible portal sign-in |
| `GET /me` | Bearer access token | Current user, assigned role, email verification state |
| `POST /register` | `email`, `user_name`, `password` (12–128 characters) | Unassigned account and confirmation-code request; 202 |
| `POST /email-verification/request` | `email` | Generic 202 response |
| `POST /email-verification/verify` | `email`, `code` (six-digit string) | Confirms email |
| `POST /password-reset/request` | `email` | Generic 202 response |
| `POST /password-reset/verify` | `email`, `code` | Single-use `reset_token`, `expires_in: 600` |
| `POST /password-reset/confirm` | `email`, `reset_token`, `new_password`, `confirm_password` | Updates password; signs out previous sessions |
| `POST /password` | Bearer token, `current_password`, `new_password` | Authenticated password change; signs out previous sessions |
| `POST /logout` | Bearer token | Revokes all current sessions for this account |
| `GET /health` | None | MySQL auth-service readiness |

New accounts always receive role 0 (`UNASSIGNED`). Confirmation does not grant a
business role: an administrator must assign an existing role before portal access.
The portal keeps its administrator-issued-account sign-in flow; no public sign-up
screen or client-controlled role selection is added.

Login with correct credentials on an unconfirmed account returns HTTP 403 with
`code: EMAIL_NOT_VERIFIED`. The portal opens its email-confirmation screen and
prefills the email in navigation state. Error responses use `detail` and `code`.

## Recovery sequence

1. Request a code for the work email.
2. Verify the six-digit string (including any leading zeros).
3. Keep the returned reset grant in memory and submit a matching new password twice.
4. Sign in normally; resetting a password never signs the user in automatically.

Codes expire after 10 minutes and are stored only as keyed HMAC hashes. Each code
allows five incorrect attempts. Sending a new code invalidates the previous code
and reset grant. Resends have a 60-second cooldown and a five-per-hour account limit.
Reset grants expire after 10 minutes and can be consumed once. Password reset also
confirms control of the same account email and sends a password-change notification.

Public requests return the same accepted response for unknown, inactive, verified
or rate-limited accounts. SMTP/account lookup runs after the HTTP response to avoid
revealing accounts through delivery latency. Provider failures are logged without
secrets; users can retry. In-process background delivery is best-effort, not a durable
mail queue. A failed password-change notification does not undo the password change.

An IP burst limit of 30 auth requests/minute complements database-backed account
limits. The IP limiter is per process; put a shared gateway limiter in front of
multiple workers. Account code/attempt counters persist in the database and use
row locks on MySQL to serialize concurrent verification/reset operations.

After password reset/change or logout, `token_version` is incremented. Middleware
rejects prior tokens, inactive accounts, unverified accounts and non-access tokens.

## Existing portal integration

The existing public layout, fields, buttons and typography are reused. The forgot
password page progresses through email, code, password and completion steps.
`/verify-email` handles email confirmation. The existing settings password form now
calls the implemented change-password endpoint and signs out on success.

Vite sends auth requests to `VITE_AUTH_BACKEND_URL` (default port 5800), while all
other API requests retain `VITE_BACKEND_URL` (the separate PostgreSQL app, default
port 8000). Restart Vite after changing proxy/environment settings. In deployment,
configure your reverse proxy to route the auth paths to the MySQL backend too.

The two backends still have separate user schemas and signing keys. This change does
not make MySQL-issued sessions valid for PostgreSQL-only APIs; the existing separation
needs a later explicit account/token integration if both applications are deployed
as one portal. The existing non-auth API code and schemas were not rewritten here.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_auth_flow.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_migrations.py -v
```

The auth checks use temporary SQLite and an in-memory fake mailer. No real emails
are sent and no configured database is modified. MySQL deployment and live SMTP
delivery still need an environment with those services running.
