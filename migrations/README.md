# Database migrations

Run commands from the backend directory. Uses the existing DATABASE_* settings in
`.env`; no application-startup migration or database creation is performed.

```powershell
.\.venv\Scripts\python.exe artisan.py migrate
.\.venv\Scripts\python.exe artisan.py migrate:status
.\.venv\Scripts\python.exe artisan.py migrate:rollback
.\.venv\Scripts\python.exe artisan.py migrate:rollback --step 2
.\.venv\Scripts\python.exe artisan.py make:migration add_phone_to_users
.\.venv\Scripts\python.exe artisan.py make:migration add_phone_to_users --autogenerate
```

Edit/review each generated upgrade and downgrade before running it. `--step`
counts individual revisions, rather than Laravel batches. Autogenerate requires
the database to be at the latest revision and models imported by migrations/env.py.

## Existing users table

For an existing table with the original `id`, `name`, `email`, `password` schema:

```powershell
.\.venv\Scripts\python.exe artisan.py migrate:baseline
.\.venv\Scripts\python.exe artisan.py migrate
```

Baseline records revision 0001 without recreating users. Existing rows/passwords
remain; migration 0002 gives them role_id=0, image=NULL, district_id=NULL and
is_active=true. The baseline command validates column names, primary key and
email uniqueness; also verify existing types/lengths match revision 0001.
Do not baseline a fresh database or an already modified users schema.

## Roles and users

| ID | Role |
| --- | --- |
| 0 | UNASSIGNED (default, no business permissions) |
| 1 | PDMA_OFFICER |
| 2 | DC |
| 3 | DONOR |
| 4 | ADMIN |

These reference roles are inserted by schema migration so role_id=0 is a valid
foreign key. Users stay in one authentication table. Existing `name` and
`password` fields and Argon2 password hashing are retained for compatibility.
`image` stores an optional image path/URL; file upload is separate future work.
`district_id` stores a nullable UUID string; its FK awaits the geography migration.
No geography/fund tables or role authorization endpoints are implemented here.
Phone/PII encryption and roadmap roles PDMA_DG/FIELD_AGENT are outside this change.
Beneficiaries do not receive self-service login accounts in the MVP specs.

## Optional demo users

Schema migrations do not create accounts. To create the five demo users listed in
specs/000-foundation/plan.md, set the actual Dadu/Buner district UUIDs, then seed:

```powershell
$env:SEED_DADU_DISTRICT_ID = '<actual Dadu district UUID>'
$env:SEED_BUNER_DISTRICT_ID = '<actual Buner district UUID>'
.\.venv\Scripts\python.exe artisan.py db:seed
# Or apply schema and seed in one command:
.\.venv\Scripts\python.exe artisan.py migrate --seed
```

The password is prompted without echo (12+ characters). For automation, provide
SEED_USER_PASSWORD through your environment. Passwords are hashed; existing
accounts are skipped without changing passwords/roles. Seeding is transactional.
Demo accounts share the supplied password; this is for local/demo use.
The donor's fund links are not seeded because fund tables do not exist yet.

## Rollback behavior

Revision 0003 adds email verification, session versions and code challenges. Its
rollback removes those fields/challenges while retaining the users and roles tables.
Existing accounts are unverified after upgrading and must confirm their email;
see [authentication setup](../docs/authentication.md).

Rolling back 0002 removes image, role_id, district_id, is_active and roles, while
retaining users' original four columns. Values in removed columns are lost.
Rolling back 0001 drops users, including data even if that table was baselined.
`artisan.py migrate:reset` rolls everything back and drops all migrated tables.
Rollback does not independently remove seeded accounts; reversing 0001 does.
Back up important data before running destructive rollbacks. MySQL DDL is not
transactional; if a migration fails partway, inspect the schema before retrying.
