"""Phase 1 WP1 contract persistence.

Revision ID: 0002_phase_1_wp1_contracts
Revises: 0001_phase_0_integrity
"""

from alembic import op

revision = "0002_phase_1_wp1_contracts"
down_revision = "0001_phase_0_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN priority integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_priority CHECK (priority >= 0)")
    op.execute(
        "ALTER TABLE approvals ADD COLUMN operation_hash text, "
        "ADD COLUMN max_uses integer NOT NULL DEFAULT 1, "
        "ADD COLUMN uses_consumed integer NOT NULL DEFAULT 0, "
        "ADD COLUMN revoked_at timestamptz"
    )
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT ck_approval_usage "
        "CHECK (max_uses >= 1 AND uses_consumed >= 0 AND uses_consumed <= max_uses)"
    )
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT ck_approval_operation_hash "
        "CHECK (operation_hash IS NULL OR operation_hash ~ '^sha256:[0-9a-f]{64}$')"
    )
    op.execute("""
        CREATE TABLE task_dependencies (
          task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
          depends_on_task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(task_id, depends_on_task_id),
          CONSTRAINT ck_task_dependency_not_self CHECK (task_id <> depends_on_task_id)
        );
        CREATE TABLE node_invocations (
          id uuid PRIMARY KEY,
          node_run_id uuid NOT NULL UNIQUE,
          run_id uuid NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
          node_id text NOT NULL,
          capability text NOT NULL,
          expected_output_contract text NOT NULL,
          status text NOT NULL DEFAULT 'CREATED',
          context_manifest_id uuid NOT NULL,
          idempotency_key text NOT NULL UNIQUE,
          request jsonb NOT NULL,
          result jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          CONSTRAINT ck_node_invocation_status CHECK (
            status IN ('CREATED','DISPATCHED','SUCCEEDED','FAILED','UNAVAILABLE','INVALID_OUTPUT')
          )
        );
        CREATE TABLE context_manifests (
          id uuid PRIMARY KEY,
          invocation_id uuid NOT NULL UNIQUE REFERENCES node_invocations(id) ON DELETE RESTRICT,
          session_id text NOT NULL,
          transport text NOT NULL,
          consumer_id text NOT NULL,
          purpose text NOT NULL,
          status text NOT NULL,
          content_hash text NOT NULL,
          document jsonb NOT NULL,
          sealed_at timestamptz NOT NULL,
          CONSTRAINT ck_context_manifest_status CHECK (status = 'SEALED'),
          CONSTRAINT ck_context_manifest_hash CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),
          CONSTRAINT uq_context_manifest_correlation UNIQUE
            (id, invocation_id, session_id, transport, content_hash)
        );
        CREATE TABLE injection_receipts (
          id uuid PRIMARY KEY,
          invocation_id uuid NOT NULL REFERENCES node_invocations(id) ON DELETE RESTRICT,
          context_manifest_id uuid NOT NULL REFERENCES context_manifests(id) ON DELETE RESTRICT,
          manifest_invocation_id uuid NOT NULL,
          manifest_context_manifest_id uuid NOT NULL,
          manifest_session_id text NOT NULL,
          manifest_transport text NOT NULL,
          status text NOT NULL,
          transport text NOT NULL,
          session_id text,
          manifest_hash text NOT NULL,
          injected_hash text,
          verification_method text,
          document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_injection_status CHECK (
            status IN ('PENDING','CONFIRMED','PARTIAL','FAILED','UNKNOWN')
          ),
          CONSTRAINT ck_injection_manifest_hash CHECK (
            manifest_hash ~ '^sha256:[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_injection_injected_hash CHECK (
            injected_hash IS NULL OR injected_hash ~ '^sha256:[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_injection_confirmed CHECK (
            status <> 'CONFIRMED' OR
            (session_id IS NOT NULL AND verification_method IS NOT NULL
             AND injected_hash = manifest_hash
             AND manifest_invocation_id = invocation_id
             AND manifest_context_manifest_id = context_manifest_id
             AND manifest_session_id = session_id
             AND manifest_transport = transport)
          ),
          CONSTRAINT fk_injection_exact_manifest FOREIGN KEY
            (manifest_context_manifest_id, manifest_invocation_id,
             manifest_session_id, manifest_transport, manifest_hash)
            REFERENCES context_manifests
            (id, invocation_id, session_id, transport, content_hash)
        );
        CREATE TABLE gate1_latch_configs (
          config_id text PRIMARY KEY,
          schema_version text NOT NULL,
          host_latch_id text NOT NULL,
          persistence text NOT NULL,
          postgresql_role text NOT NULL,
          independent_of_llm boolean NOT NULL,
          independent_of_langgraph boolean NOT NULL,
          independent_of_openclaw boolean NOT NULL,
          guard_before_work_acquisition boolean NOT NULL,
          guard_before_effect boolean NOT NULL,
          health_recovery_releases_latch boolean NOT NULL,
          production_release text NOT NULL,
          document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_gate1_latch_inert_config CHECK (
            config_id = 'gate1-latch' AND schema_version = '1.0.0'
            AND persistence = 'HOST_PERSISTENT'
            AND postgresql_role = 'PROJECTION_AND_AUDIT_ONLY'
            AND independent_of_llm AND independent_of_langgraph
            AND independent_of_openclaw AND guard_before_work_acquisition
            AND guard_before_effect AND NOT health_recovery_releases_latch
            AND production_release = 'DEFERRED_NOT_IMPLEMENTED'
          )
        );
        CREATE TABLE tool_calls (
          id uuid PRIMARY KEY,
          operation_id uuid NOT NULL UNIQUE,
          action_id uuid NOT NULL REFERENCES actions(id) ON DELETE RESTRICT,
          run_id uuid NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
          tool_id text NOT NULL,
          actor_id text NOT NULL,
          state text NOT NULL DEFAULT 'CREATED',
          operation_hash text NOT NULL,
          idempotency_key text NOT NULL,
          request jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(actor_id, idempotency_key),
          CONSTRAINT ck_g1_tool CHECK (tool_id = 'fixture.atomic_write'),
          CONSTRAINT ck_tool_call_state CHECK (
            state IN ('CREATED','DISPATCHED','IN_PROGRESS','SUCCEEDED','FAILED',
                      'TIMED_OUT','OUTCOME_UNKNOWN','CANCELLED')
          ),
          CONSTRAINT ck_tool_operation_hash CHECK (
            operation_hash ~ '^sha256:[0-9a-f]{64}$'
          )
        );
        CREATE TABLE effect_receipts (
          id uuid PRIMARY KEY,
          tool_call_id uuid NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE RESTRICT,
          outcome text NOT NULL,
          previous_hash text,
          resulting_hash text,
          verified boolean NOT NULL,
          document jsonb NOT NULL,
          observed_at timestamptz NOT NULL,
          CONSTRAINT ck_effect_outcome CHECK (
            outcome IN ('SUCCEEDED','FAILED','OUTCOME_UNKNOWN')
          ),
          CONSTRAINT ck_effect_previous_hash CHECK (
            previous_hash IS NULL OR previous_hash ~ '^sha256:[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_effect_resulting_hash CHECK (
            resulting_hash IS NULL OR resulting_hash ~ '^sha256:[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_effect_semantics CHECK (
            (outcome = 'SUCCEEDED' AND verified AND resulting_hash IS NOT NULL) OR
            (outcome = 'FAILED') OR
            (outcome = 'OUTCOME_UNKNOWN' AND NOT verified)
          )
        );
        CREATE TABLE reconciliations (
          id uuid PRIMARY KEY,
          tool_call_id uuid NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
          operation_id uuid NOT NULL,
          outcome text NOT NULL,
          reason_code text NOT NULL,
          evidence_refs jsonb NOT NULL,
          document jsonb NOT NULL,
          reconciled_at timestamptz NOT NULL,
          CONSTRAINT ck_reconciliation_outcome CHECK (
            outcome IN ('NO_EFFECT','CONFIRMED_SUCCEEDED','CONFIRMED_FAILED','UNRESOLVED')
          )
        );
        CREATE INDEX ix_task_dependencies_depends_on ON task_dependencies(depends_on_task_id);
        CREATE INDEX ix_node_invocations_run ON node_invocations(run_id);
        CREATE INDEX ix_injection_receipts_manifest ON injection_receipts(context_manifest_id);
        CREATE INDEX ix_reconciliations_tool_call ON reconciliations(tool_call_id);
    """)


def downgrade() -> None:
    op.execute(
        "DROP TABLE reconciliations, effect_receipts, tool_calls, gate1_latch_configs, injection_receipts, "
        "context_manifests, node_invocations, task_dependencies CASCADE"
    )
    op.execute(
        "ALTER TABLE approvals DROP CONSTRAINT ck_approval_operation_hash, "
        "DROP CONSTRAINT ck_approval_usage, DROP COLUMN revoked_at, "
        "DROP COLUMN uses_consumed, DROP COLUMN max_uses, DROP COLUMN operation_hash"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_priority, DROP COLUMN priority")
