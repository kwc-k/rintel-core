"""Persist lifecycle and closure receipts on existing PostgreSQL installations."""
from alembic import op

revision = "0006_design_closure_receipts"
down_revision = "0005_atomic_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS design_changes (
      id TEXT PRIMARY KEY, repo_id TEXT NOT NULL,
      base_canonical_revision TEXT NOT NULL, state TEXT NOT NULL,
      version INT NOT NULL, aggregate_json JSONB NOT NULL,
      created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_design_changes_repo"
               " ON design_changes(repo_id, created_at)")
    op.execute("""CREATE TABLE IF NOT EXISTS design_revisions (
      id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
      revision_json JSONB NOT NULL, digest TEXT NOT NULL,
      created_at BIGINT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS design_lifecycle_receipts (
      id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
      sequence INT NOT NULL, command TEXT NOT NULL, actor TEXT NOT NULL,
      state_before TEXT, state_after TEXT NOT NULL, payload_json JSONB NOT NULL,
      digest TEXT NOT NULL, created_at BIGINT NOT NULL,
      UNIQUE(change_id, sequence))""")
    op.execute("""CREATE TABLE IF NOT EXISTS design_coverage_certificates (
      id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, canonical_revision TEXT NOT NULL,
      subject TEXT NOT NULL, relation_kind TEXT NOT NULL,
      payload_json JSONB NOT NULL, created_at BIGINT NOT NULL)""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_design_coverage_lookup"
               " ON design_coverage_certificates(repo_id, canonical_revision,"
               " subject, relation_kind, created_at)")
    for kind in ("test", "approval"):
        op.execute(f"""CREATE TABLE IF NOT EXISTS design_{kind}_receipts (
          id TEXT PRIMARY KEY,
          change_id TEXT NOT NULL REFERENCES design_changes(id),
          payload_json JSONB NOT NULL, digest TEXT NOT NULL,
          created_at BIGINT NOT NULL)""")
    op.execute("""CREATE OR REPLACE FUNCTION reject_design_immutable_mutation()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'design lifecycle immutable row'; END $$""")
    for table, trigger in (
        ("design_revisions", "design_revisions_no_update"),
        ("design_lifecycle_receipts", "design_receipts_no_update"),
        ("design_coverage_certificates", "design_coverage_no_update"),
        ("design_test_receipts", "design_test_no_update"),
        ("design_approval_receipts", "design_approval_no_update"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE"
                   f" ON {table} FOR EACH ROW"
                   " EXECUTE FUNCTION reject_design_immutable_mutation()")


def downgrade() -> None:
    for table in ("design_approval_receipts", "design_test_receipts",
                  "design_coverage_certificates"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    # Existing lifecycle data is retained; this migration may have been run on
    # an installation where those tables predated Alembic.
