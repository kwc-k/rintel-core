"""INDEX-INCREMENTAL0 per-input semantic digest."""
from alembic import op

revision = "0004_incremental_semantic_digest"
down_revision = "0003_flow_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS semantic_digest TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS semantic_digest")
