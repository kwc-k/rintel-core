"""INDEX-INCREMENTAL0 explicit staging/published snapshot state."""
from alembic import op

revision = "0005_atomic_publication"
down_revision = "0004_incremental_semantic_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS publication_status "
        "TEXT NOT NULL DEFAULT 'published'")


def downgrade() -> None:
    op.execute("ALTER TABLE snapshots DROP COLUMN IF EXISTS publication_status")
