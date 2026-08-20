#!/usr/bin/env bash
set -euo pipefail

migration_db="jarvis_migration_gate0"
docker compose exec -T postgres dropdb -U jarvis --if-exists "$migration_db"
docker compose exec -T postgres createdb -U jarvis "$migration_db"
JARVIS_DATABASE_DSN="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$migration_db" \
  uv run alembic upgrade 0001_phase_0_integrity
version="$(docker compose exec -T postgres psql -U jarvis -d "$migration_db" -Atc \
  "SELECT version_num FROM alembic_version;")"
table_count="$(docker compose exec -T postgres psql -U jarvis -d "$migration_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('tasks','runs','leases','approvals','outbox','inbox','idempotency_records','audit_records');")"
test "$version" = "0001_phase_0_integrity"
test "$table_count" = "8"
printf 'migration=%s tables=%s\n' "$version" "$table_count"
