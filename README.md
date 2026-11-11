"# ab_o_hisaab_backend" 

## Migrations

Laravel-style commands are available through the project Python:

```powershell
.\.venv\Scripts\python.exe artisan.py migrate
.\.venv\Scripts\python.exe artisan.py migrate:rollback
.\.venv\Scripts\python.exe artisan.py migrate:status
.\.venv\Scripts\python.exe artisan.py make:migration add_phone_to_users
```

If the original users table already exists, run `artisan.py migrate:baseline`
before the first `migrate`. Optional demo accounts: `artisan.py migrate --seed`
or `artisan.py db:seed` (requires district UUIDs and a password).

See [migration instructions](migrations/README.md) for seed setup, role IDs,
existing-database adoption and rollback data-loss behavior.

Verify migration lifecycle without touching the configured database:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_migrations.py -v
```
