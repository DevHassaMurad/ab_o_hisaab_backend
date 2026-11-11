#!/bin/sh
# Runs only while the PostgreSQL named volume is initialized for the first time.
set -e

if [ -z "$POSTGRES_TEST_DB" ] || [ "$POSTGRES_TEST_DB" = "$POSTGRES_DB" ]; then
    echo 'POSTGRES_TEST_DB must be non-empty and different from POSTGRES_DB.' >&2
    exit 1
fi

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 --set=test_db="$POSTGRES_TEST_DB" <<'SQL'
SELECT format('CREATE DATABASE %I', :'test_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'test_db') \gexec
CREATE EXTENSION IF NOT EXISTS postgis;
SQL

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_TEST_DB" \
    --set=ON_ERROR_STOP=1 --command 'CREATE EXTENSION IF NOT EXISTS postgis;'
