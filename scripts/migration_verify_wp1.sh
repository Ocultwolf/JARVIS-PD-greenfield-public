#!/usr/bin/env bash
set -euo pipefail

empty_db="jarvis_migration_wp1_empty"
upgrade_db="jarvis_migration_wp1_upgrade"
downgrade_db="jarvis_migration_wp1_downgrade"

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  docker compose exec -T postgres dropdb -U jarvis --if-exists "$database"
  docker compose exec -T postgres createdb -U jarvis "$database"
done

empty_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$empty_db"
upgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$upgrade_db"
downgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$downgrade_db"

JARVIS_DATABASE_DSN="$empty_dsn" uv run alembic upgrade 0002_phase_1_wp1_contracts

JARVIS_DATABASE_DSN="$upgrade_dsn" uv run alembic upgrade 0001_phase_0_integrity
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U jarvis -d "$upgrade_db" <<'SQL'
INSERT INTO goals(id,title,state) VALUES
  ('00000000-0000-0000-0000-000000000001','G0 preserved goal','ACTIVE');
INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state) VALUES
  ('00000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   'G0 preserved task','["preserved"]'::jsonb,'READY');
SQL
JARVIS_DATABASE_DSN="$upgrade_dsn" uv run alembic upgrade 0002_phase_1_wp1_contracts
JARVIS_DATABASE_DSN="$downgrade_dsn" uv run alembic upgrade 0002_phase_1_wp1_contracts

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  version="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT version_num FROM alembic_version;")"
  table_count="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('task_dependencies','node_invocations','context_manifests','injection_receipts','gate1_latch_configs','tool_calls','effect_receipts','reconciliations');")"
  test "$version" = "0002_phase_1_wp1_contracts"
  test "$table_count" = "8"
done

JARVIS_DATABASE_DSN="$downgrade_dsn" uv run alembic downgrade 0001_phase_0_integrity
downgraded_version="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT version_num FROM alembic_version;")"
downgraded_wp1_tables="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('task_dependencies','node_invocations','context_manifests','injection_receipts','gate1_latch_configs','tool_calls','effect_receipts','reconciliations');")"
downgraded_g0_tables="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('tasks','runs','leases','approvals','outbox','inbox','idempotency_records','audit_records');")"
test "$downgraded_version" = "0001_phase_0_integrity"
test "$downgraded_wp1_tables" = "0"
test "$downgraded_g0_tables" = "8"

preserved="$(docker compose exec -T postgres psql -U jarvis -d "$upgrade_db" -Atc \
  "SELECT count(*) FROM tasks WHERE id='00000000-0000-0000-0000-000000000002' AND priority=0;")"
test "$preserved" = "1"

printf 'empty=%s upgrade=%s tables=8 g0_rows_preserved=%s downgrade=%s wp1_tables_after_downgrade=%s g0_tables_after_downgrade=%s\n' \
  "0002_phase_1_wp1_contracts" "0002_phase_1_wp1_contracts" "$preserved" \
  "$downgraded_version" "$downgraded_wp1_tables" "$downgraded_g0_tables"
