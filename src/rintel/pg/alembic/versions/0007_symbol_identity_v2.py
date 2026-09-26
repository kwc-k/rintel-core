"""Add versioned symbol identity without rewriting historical evidence."""
from alembic import op

revision = "0007_symbol_identity_v2"
down_revision = "0006_design_closure_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE symbols ADD COLUMN IF NOT EXISTS "
        "identity_schema_version TEXT NOT NULL DEFAULT 'symbol-identity/v1'")
    op.execute(
        "ALTER TABLE symbols DROP CONSTRAINT IF EXISTS "
        "symbols_repo_id_kind_qname_key")
    op.execute(
        "ALTER TABLE snapshot_symbols DROP CONSTRAINT IF EXISTS "
        "snapshot_symbols_repo_id_snapshot_id_kind_qname_key")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ss_legacy_lookup ON "
        "snapshot_symbols(repo_id, snapshot_id, kind, qname)")


def downgrade() -> None:
    # Restoring qname uniqueness could discard valid v2 collisions.  Historical
    # rows are intentionally not rewritten, and a destructive downgrade is
    # deliberately unavailable.
    raise RuntimeError("symbol-identity/v2 migration cannot be downgraded safely")
