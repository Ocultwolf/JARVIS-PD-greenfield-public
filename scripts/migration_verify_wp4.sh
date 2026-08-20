#!/usr/bin/env bash
set -euo pipefail

empty_db="jarvis_migration_wp4_empty"
upgrade_db="jarvis_migration_wp4_upgrade"
downgrade_db="jarvis_migration_wp4_downgrade"

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  docker compose exec -T postgres dropdb -U jarvis --if-exists "$database"
  docker compose exec -T postgres createdb -U jarvis "$database"
done

empty_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$empty_db"
upgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$upgrade_db"
downgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$downgrade_db"

JARVIS_DATABASE_DSN="$empty_dsn" .venv/bin/alembic upgrade head
JARVIS_DATABASE_DSN="$upgrade_dsn" .venv/bin/alembic upgrade 0003_phase_1_wp3_effects
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U jarvis -d "$upgrade_db" <<'SQL'
INSERT INTO goals(id,title,state) VALUES
  ('00000000-0000-0000-0000-000000000001','WP4 preserved goal','ACTIVE');
INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority) VALUES
  ('00000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   'WP4 preserved task','["preserved"]'::jsonb,'READY',0);
SQL
JARVIS_DATABASE_DSN="$upgrade_dsn" .venv/bin/alembic upgrade head
JARVIS_DATABASE_DSN="$downgrade_dsn" .venv/bin/alembic upgrade head

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  version="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT version_num FROM alembic_version;")"
  wp4_tables="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('worker_heartbeats','worker_interruptions','gate1_local_latch');")"
  test "$version" = "0004_phase_1_wp4_continuity"
  test "$wp4_tables" = "3"
done

JARVIS_DATABASE_DSN="$downgrade_dsn" .venv/bin/alembic downgrade 0003_phase_1_wp3_effects
downgraded_version="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT version_num FROM alembic_version;")"
wp4_tables_after="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('worker_heartbeats','worker_interruptions','gate1_local_latch');")"
wp3_tables="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('compensation_operations','compensation_reconciliations');")"
preserved="$(docker compose exec -T postgres psql -U jarvis -d "$upgrade_db" -Atc \
  "SELECT count(*) FROM tasks WHERE id='00000000-0000-0000-0000-000000000002' AND priority=0;")"

test "$downgraded_version" = "0003_phase_1_wp3_effects"
test "$wp4_tables_after" = "0"
test "$wp3_tables" = "2"
test "$preserved" = "1"

printf 'empty=%s upgrade=%s preserved=%s downgrade=%s wp4_after=%s wp3_tables=%s\n' \
  "0004_phase_1_wp4_continuity" "0004_phase_1_wp4_continuity" "$preserved" \
  "$downgraded_version" "$wp4_tables_after" "$wp3_tables"
