"""Phase 1 WP3 approval and effect execution persistence.

Revision ID: 0003_phase_1_wp3_effects
Revises: 0002_phase_1_wp1_contracts
"""

from alembic import op

revision = "0003_phase_1_wp3_effects"
down_revision = "0002_phase_1_wp1_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE approvals ADD COLUMN requested_by text, "
        "ADD COLUMN request_document jsonb, ADD COLUMN decision_document jsonb"
    )
    op.execute(
        "ALTER TABLE tool_calls ADD COLUMN previous_content bytea, "
        "ADD COLUMN resulting_content bytea, ADD COLUMN approval_id uuid REFERENCES approvals(id), "
        "ADD COLUMN owner_id text, ADD COLUMN fencing_token bigint"
    )
    op.execute(
        "ALTER TABLE effect_receipts ADD COLUMN operation_id uuid, "
        "ADD COLUMN owner_id text, ADD COLUMN fencing_token bigint, "
        "ADD COLUMN idempotency_key text"
    )
    op.execute(
        "ALTER TABLE reconciliations ADD COLUMN owner_id text, "
        "ADD COLUMN fencing_token bigint, ADD COLUMN idempotency_key text, "
        "ADD CONSTRAINT uq_reconciliation_idempotency UNIQUE(tool_call_id,idempotency_key)"
    )
    op.execute("""
        CREATE TABLE compensation_operations (
          id uuid PRIMARY KEY,
          original_tool_call_id uuid NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
          run_id uuid NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
          operation_id uuid NOT NULL UNIQUE,
          operation_hash text NOT NULL,
          actor_id text NOT NULL,
          idempotency_key text NOT NULL,
          approval_id uuid NOT NULL REFERENCES approvals(id) ON DELETE RESTRICT,
          owner_id text NOT NULL,
          fencing_token bigint NOT NULL,
          state text NOT NULL,
          request jsonb NOT NULL,
          receipt jsonb,
          previous_content bytea,
          expected_result_hash text NOT NULL,
          observed_result_hash text,
          error_code text,
          run_state_before_unknown run_state,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(actor_id,idempotency_key),
          CONSTRAINT ck_compensation_state CHECK (
            state IN ('CREATED','IN_PROGRESS','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')
          ),
          CONSTRAINT ck_compensation_hash CHECK (
            operation_hash ~ '^sha256:[0-9a-f]{64}$'
          )
        )
    """)
    op.execute("""
        CREATE TABLE compensation_reconciliations (
          id uuid PRIMARY KEY,
          compensation_id uuid NOT NULL REFERENCES compensation_operations(id) ON DELETE RESTRICT,
          operation_id uuid NOT NULL,
          outcome text NOT NULL,
          observed_hash text,
          evidence_refs jsonb NOT NULL,
          document jsonb NOT NULL,
          reconciled_at timestamptz NOT NULL,
          owner_id text NOT NULL,
          fencing_token bigint NOT NULL,
          idempotency_key text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(compensation_id,idempotency_key),
          CONSTRAINT ck_compensation_reconciliation_outcome CHECK (
            outcome IN ('COMPENSATION_CONFIRMED','COMPENSATION_NOT_OCCURRED','UNRESOLVED')
          )
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE compensation_reconciliations")
    op.execute("DROP TABLE compensation_operations")
    op.execute(
        "ALTER TABLE reconciliations DROP CONSTRAINT uq_reconciliation_idempotency, "
        "DROP COLUMN idempotency_key, DROP COLUMN fencing_token, DROP COLUMN owner_id"
    )
    op.execute(
        "ALTER TABLE effect_receipts DROP COLUMN idempotency_key, DROP COLUMN fencing_token, "
        "DROP COLUMN owner_id, DROP COLUMN operation_id"
    )
    op.execute(
        "ALTER TABLE tool_calls DROP COLUMN fencing_token, DROP COLUMN owner_id, "
        "DROP COLUMN approval_id, DROP COLUMN resulting_content, DROP COLUMN previous_content"
    )
    op.execute(
        "ALTER TABLE approvals DROP COLUMN decision_document, DROP COLUMN request_document, "
        "DROP COLUMN requested_by"
    )
