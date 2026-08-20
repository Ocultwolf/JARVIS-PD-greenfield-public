#!/usr/bin/env bash
set -euo pipefail

dump_path="${1:?usage: restore_verify_gate0.sh DUMP_PATH}"
restore_db="jarvis_restore_gate0"

sha256sum --check "$dump_path.sha256"
docker compose exec -T postgres dropdb -U jarvis --if-exists "$restore_db"
docker compose exec -T postgres createdb -U jarvis "$restore_db"
docker compose exec -T postgres pg_restore -U jarvis -d "$restore_db" --exit-on-error \
  < "$dump_path"
restored_snapshot="$(docker compose exec -T postgres psql -U jarvis -d "$restore_db" -Atc \
  "SELECT json_build_object('migration',(SELECT version_num FROM alembic_version),'goals',(SELECT count(*) FROM goals),'tasks',(SELECT count(*) FROM tasks),'runs',(SELECT count(*) FROM runs),'actions',(SELECT count(*) FROM actions),'evidence',(SELECT count(*) FROM evidence),'approvals',(SELECT count(*) FROM approvals),'leases',(SELECT count(*) FROM leases),'audit_records',(SELECT count(*) FROM audit_records),'inbox',(SELECT count(*) FROM inbox),'inbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM inbox GROUP BY state ORDER BY state) s),'{}'::json),'outbox',(SELECT count(*) FROM outbox),'outbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM outbox GROUP BY state ORDER BY state) s),'{}'::json),'idempotency_records',(SELECT count(*) FROM idempotency_records));")"
expected_snapshot="$(tr -d '\r\n' < "$dump_path.snapshot.json")"
test "$restored_snapshot" = "$expected_snapshot"
sleep 1
stable_snapshot="$(docker compose exec -T postgres psql -U jarvis -d "$restore_db" -Atc \
  "SELECT json_build_object('migration',(SELECT version_num FROM alembic_version),'goals',(SELECT count(*) FROM goals),'tasks',(SELECT count(*) FROM tasks),'runs',(SELECT count(*) FROM runs),'actions',(SELECT count(*) FROM actions),'evidence',(SELECT count(*) FROM evidence),'approvals',(SELECT count(*) FROM approvals),'leases',(SELECT count(*) FROM leases),'audit_records',(SELECT count(*) FROM audit_records),'inbox',(SELECT count(*) FROM inbox),'inbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM inbox GROUP BY state ORDER BY state) s),'{}'::json),'outbox',(SELECT count(*) FROM outbox),'outbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM outbox GROUP BY state ORDER BY state) s),'{}'::json),'idempotency_records',(SELECT count(*) FROM idempotency_records));")"
test "$stable_snapshot" = "$restored_snapshot"
printf 'NO_AUTOMATIC_REPLAY\n'
printf '%s\n' "$restored_snapshot"
