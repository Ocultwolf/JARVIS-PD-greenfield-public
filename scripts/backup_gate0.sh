#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:?usage: backup_gate0.sh OUTPUT_DIR}"
mkdir -p "$output_dir"
backup_id="gate0-$(date -u +%Y%m%dT%H%M%SZ)"
dump_path="$output_dir/$backup_id.dump"

docker compose exec -T postgres pg_dump -U jarvis -d jarvis -Fc > "$dump_path"
sha256sum "$dump_path" > "$dump_path.sha256"
docker compose exec -T postgres psql -U jarvis -d jarvis -Atc \
  "SELECT json_build_object('migration',(SELECT version_num FROM alembic_version),'goals',(SELECT count(*) FROM goals),'tasks',(SELECT count(*) FROM tasks),'runs',(SELECT count(*) FROM runs),'actions',(SELECT count(*) FROM actions),'evidence',(SELECT count(*) FROM evidence),'approvals',(SELECT count(*) FROM approvals),'leases',(SELECT count(*) FROM leases),'audit_records',(SELECT count(*) FROM audit_records),'inbox',(SELECT count(*) FROM inbox),'inbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM inbox GROUP BY state ORDER BY state) s),'{}'::json),'outbox',(SELECT count(*) FROM outbox),'outbox_states',COALESCE((SELECT json_object_agg(state,total) FROM (SELECT state,count(*) AS total FROM outbox GROUP BY state ORDER BY state) s),'{}'::json),'idempotency_records',(SELECT count(*) FROM idempotency_records));" \
  > "$dump_path.snapshot.json"
printf '%s\n' "$dump_path"
