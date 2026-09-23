"""FAC-EQ0: callsite call-shape metadata (form/args) for unresolved-evidence
quality classification."""
from alembic import op

revision = "0002_eq0_callsite_shape"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE callsites ADD COLUMN shape JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE callsites DROP COLUMN shape")
