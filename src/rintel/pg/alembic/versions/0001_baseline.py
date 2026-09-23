"""Baseline migration: the frozen SPEC-P1 §6.2 schema (v1.1-fix1)."""
from alembic import op
from sqlalchemy import text

from rintel.pg.pgschema import (ALL_TABLES, DDL_STATEMENTS,
                                TRGM_INDEX_STATEMENTS)

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    has_trgm = conn.execute(text(
        "SELECT count(*) FROM pg_available_extensions"
        " WHERE name='pg_trgm'")).scalar() > 0
    if has_trgm:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for stmt in DDL_STATEMENTS:
        op.execute(stmt)
    if has_trgm:
        for stmt in TRGM_INDEX_STATEMENTS:
            op.execute(stmt)


def downgrade() -> None:
    for table in ALL_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
