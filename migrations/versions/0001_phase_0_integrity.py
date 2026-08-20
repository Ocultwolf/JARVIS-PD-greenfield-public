"""Phase 0 integrity schema.

Revision ID: 0001_phase_0_integrity
Revises: None
"""

from alembic import op

revision = "0001_phase_0_integrity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE task_state AS ENUM ('PROPOSED','READY','BLOCKED','WAITING_APPROVAL','RUNNING','VALIDATING','COMPLETED','FAILED','CANCELLED','SUPERSEDED')"
    )
    op.execute(
        "CREATE TYPE run_state AS ENUM ('CREATED','READY','RUNNING','PAUSED','BLOCKED','RECONCILIATION_REQUIRED','SUCCEEDED','FAILED','CANCELLED')"
    )
    op.execute(
        "CREATE TYPE approval_state AS ENUM ('PENDING','APPROVED','DENIED','EXPIRED','REVOKED','CONSUMED','INVALIDATED')"
    )
    op.execute("""
        CREATE TABLE goals (
          id uuid PRIMARY KEY, title text NOT NULL, state text NOT NULL DEFAULT 'DRAFT',
          revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE tasks (
          id uuid PRIMARY KEY, goal_id uuid REFERENCES goals(id), title text NOT NULL,
          acceptance_criteria jsonb NOT NULL, state task_state NOT NULL DEFAULT 'PROPOSED',
          revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE leases (
          resource_type text NOT NULL, resource_id uuid NOT NULL, owner_id text NOT NULL,
          fencing_token bigint NOT NULL, state text NOT NULL, expires_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(resource_type, resource_id)
        );
        CREATE TABLE runs (
          id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id), state run_state NOT NULL,
          fencing_token bigint, revision integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_active_run_per_task ON runs(task_id)
          WHERE state IN ('CREATED','READY','RUNNING','PAUSED','BLOCKED','RECONCILIATION_REQUIRED');
        CREATE TABLE actions (
          id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id), kind text NOT NULL,
          state text NOT NULL, revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE evidence (
          id uuid PRIMARY KEY, run_id uuid REFERENCES runs(id), kind text NOT NULL,
          payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE approvals (
          id uuid PRIMARY KEY, operation_id uuid NOT NULL UNIQUE, state approval_state NOT NULL,
          requested_from text NOT NULL, decided_by text, decision_reason text,
          expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), decided_at timestamptz
        );
        CREATE TABLE outbox (
          id uuid PRIMARY KEY, message_type text NOT NULL, aggregate_type text NOT NULL,
          aggregate_id uuid NOT NULL, payload jsonb NOT NULL, state text NOT NULL DEFAULT 'QUEUED',
          attempts integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
          released_at timestamptz
        );
        CREATE TABLE inbox (
          message_id uuid PRIMARY KEY, message_type text NOT NULL, payload_hash text NOT NULL,
          state text NOT NULL DEFAULT 'RECEIVED', received_at timestamptz NOT NULL DEFAULT now(),
          released_at timestamptz
        );
        CREATE TABLE idempotency_records (
          actor_id text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
          state text NOT NULL, result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id, idempotency_key)
        );
        CREATE TABLE audit_records (
          sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id uuid NOT NULL UNIQUE,
          request_id uuid NOT NULL, actor_id text NOT NULL, command_type text NOT NULL,
          aggregate_type text NOT NULL, aggregate_id uuid NOT NULL, approval_id uuid,
          previous_state text, new_state text, outcome text NOT NULL, details jsonb NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute(
        "DROP TABLE audit_records, idempotency_records, inbox, outbox, approvals, evidence, actions, runs, leases, tasks, goals CASCADE"
    )
    op.execute("DROP TYPE approval_state, run_state, task_state")
