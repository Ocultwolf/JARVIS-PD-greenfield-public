"""Phase 1 WP4 local worker continuity persistence.

Revision ID: 0004_phase_1_wp4_continuity
Revises: 0003_phase_1_wp3_effects
"""

from alembic import op

revision = "0004_phase_1_wp4_continuity"
down_revision = "0003_phase_1_wp3_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE worker_heartbeats (
          worker_id text PRIMARY KEY,
          owner_id text NOT NULL,
          status text NOT NULL,
          last_seen_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          revision bigint NOT NULL DEFAULT 1,
          CONSTRAINT ck_worker_heartbeat_status CHECK (
            status IN ('IDLE','RUNNING','INTERRUPTED','STOPPED')
          ),
          CONSTRAINT ck_worker_heartbeat_window CHECK (expires_at > last_seen_at)
        );
        CREATE TABLE worker_interruptions (
          id uuid PRIMARY KEY,
          run_id uuid NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
          owner_id text NOT NULL,
          fencing_token bigint NOT NULL,
          phase text NOT NULL,
          classification text NOT NULL,
          last_confirmed_state run_state NOT NULL,
          correlation_id uuid NOT NULL,
          causation_id uuid,
          details jsonb NOT NULL,
          observed_at timestamptz NOT NULL,
          recovered_at timestamptz,
          recovered_by text,
          recovery_fencing_token bigint,
          CONSTRAINT ck_worker_interruption_class CHECK (
            classification IN (
              'PRE_DISPATCH_NO_EFFECT','POST_DISPATCH_CONFIRMED_FAILED',
              'POST_DISPATCH_UNKNOWN','NON_RECOVERABLE'
            )
          )
        );
        CREATE INDEX ix_worker_interruptions_run_open
          ON worker_interruptions(run_id) WHERE recovered_at IS NULL;
        CREATE TABLE gate1_local_latch (
          latch_id text PRIMARY KEY,
          active boolean NOT NULL,
          reason text NOT NULL,
          revision bigint NOT NULL DEFAULT 1,
          host_state_hash text NOT NULL,
          observed_at timestamptz NOT NULL,
          CONSTRAINT ck_gate1_local_latch_id CHECK (latch_id = 'gate1-local'),
          CONSTRAINT ck_gate1_local_latch_hash CHECK (
            host_state_hash ~ '^sha256:[0-9a-f]{64}$'
          )
        );
        ALTER TABLE outbox ADD COLUMN delivery_owner text,
          ADD COLUMN delivery_started_at timestamptz,
          ADD COLUMN last_delivery_error text;
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE outbox DROP COLUMN IF EXISTS last_delivery_error, "
        "DROP COLUMN IF EXISTS delivery_started_at, DROP COLUMN IF EXISTS delivery_owner"
    )
    op.execute("DROP TABLE gate1_local_latch, worker_interruptions, worker_heartbeats")
