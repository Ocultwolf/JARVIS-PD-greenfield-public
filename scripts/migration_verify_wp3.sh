#!/usr/bin/env bash
set -euo pipefail

empty_db="jarvis_migration_wp3_empty"
upgrade_db="jarvis_migration_wp3_upgrade"
downgrade_db="jarvis_migration_wp3_downgrade"

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  docker compose exec -T postgres dropdb -U jarvis --if-exists "$database"
  docker compose exec -T postgres createdb -U jarvis "$database"
done

empty_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$empty_db"
upgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$upgrade_db"
downgrade_dsn="postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/$downgrade_db"

JARVIS_DATABASE_DSN="$empty_dsn" .venv/bin/alembic upgrade 0003_phase_1_wp3_effects
JARVIS_DATABASE_DSN="$upgrade_dsn" .venv/bin/alembic upgrade 0002_phase_1_wp1_contracts
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U jarvis -d "$upgrade_db" <<'SQL'
INSERT INTO goals(id,title,state) VALUES
  ('00000000-0000-0000-0000-000000000001','G0 preserved goal','ACTIVE');
INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state) VALUES
  ('00000000-0000-0000-0000-000000000002',
   '00000000-0000-0000-0000-000000000001',
   'WP1 preserved task','["preserved"]'::jsonb,'READY');
SQL
JARVIS_DATABASE_DSN="$upgrade_dsn" .venv/bin/alembic upgrade 0003_phase_1_wp3_effects
JARVIS_DATABASE_DSN="$downgrade_dsn" .venv/bin/alembic upgrade 0003_phase_1_wp3_effects

for database in "$empty_db" "$upgrade_db" "$downgrade_db"; do
  version="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT version_num FROM alembic_version;")"
  compensation_tables="$(docker compose exec -T postgres psql -U jarvis -d "$database" -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='compensation_operations';")"
  test "$version" = "0003_phase_1_wp3_effects"
  test "$compensation_tables" = "1"
done

JARVIS_DATABASE_DSN="$downgrade_dsn" .venv/bin/alembic downgrade 0002_phase_1_wp1_contracts
downgraded_version="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT version_num FROM alembic_version;")"
wp3_tables="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='compensation_operations';")"
wp1_tables="$(docker compose exec -T postgres psql -U jarvis -d "$downgrade_db" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('tool_calls','effect_receipts','reconciliations');")"
test "$downgraded_version" = "0002_phase_1_wp1_contracts"
test "$wp3_tables" = "0"
test "$wp1_tables" = "3"

preserved="$(docker compose exec -T postgres psql -U jarvis -d "$upgrade_db" -Atc \
  "SELECT count(*) FROM tasks WHERE id='00000000-0000-0000-0000-000000000002' AND priority=0;")"
test "$preserved" = "1"

printf 'empty=%s upgrade=%s preserved=%s downgrade=%s wp3_tables=%s wp1_tables=%s\n' \
  "0003_phase_1_wp3_effects" "0003_phase_1_wp3_effects" "$preserved" \
  "$downgraded_version" "$wp3_tables" "$wp1_tables"
