"""PostgreSQL baseline DDL (SPEC-P1 §6.2, v1.1-fix1).

Mirrors the amended schema contract: evidence plane (repositories / snapshots /
files / symbols / snapshot_symbols / snapshot_edges / symbol_evidence /
edge_evidence / callsites / imports / includes / pending_edges / unresolved /
run_telemetry) and architecture plane (arch_workspaces / arch_models / designs
removed by fix1 / arch_components / arch_relations / arch_mappings /
arch_annotations / arch_layouts / workspace_ui_state / jobs).

Invariants enforced here (SPEC-P1 §6.1):
- every FK carries repo_id (composite FKs keep cross-table repo consistency);
- core semantic invariants → DB constraint (component belongs to model;
  relation src/dst in same workspace+model; parent in same model; one AS-IS
  model per workspace);
- secondary consistency (annotations/layout/ui-state) → single-column FKs only;
- `baseline_json` is the single JSONB identity exception (immutable snapshot
  document, `baseline_schema_version = 1`); live identity/FKs never JSONB-only.
"""
from __future__ import annotations

# fmt: off
DDL_STATEMENTS: list[str] = [
    # ------------------------------------------------------------------
    # Evidence plane
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS repositories (
      id          TEXT PRIMARY KEY,
      root_path   TEXT NOT NULL,
      created_at  BIGINT NOT NULL,                 -- ms epoch
      meta        JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
      id          TEXT PRIMARY KEY,
      repo_id     TEXT NOT NULL REFERENCES repositories(id),
      parent_id   TEXT,
      commit_sha  TEXT,
      publication_status TEXT NOT NULL DEFAULT 'published',
      created_at  BIGINT NOT NULL,
      meta        JSONB NOT NULL DEFAULT '{}',
      UNIQUE (repo_id, id),
      FOREIGN KEY (repo_id, parent_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshots_repo ON snapshots(repo_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS files (
      repo_id         TEXT NOT NULL REFERENCES repositories(id),
      path            TEXT NOT NULL,
      hash            TEXT NOT NULL,
      size            BIGINT NOT NULL,
      parser_version  TEXT,
      indexer_version TEXT,
      semantic_digest TEXT,
      status          TEXT NOT NULL DEFAULT 'ok',
      last_indexed_at BIGINT,
      PRIMARY KEY (repo_id, path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS symbols (
      repo_id            TEXT NOT NULL REFERENCES repositories(id),
      id                 TEXT NOT NULL,             -- versioned canonical ID
      kind               TEXT NOT NULL,
      name               TEXT NOT NULL,
      qname              TEXT NOT NULL,
      language           TEXT NOT NULL,
      identity_schema_version TEXT NOT NULL DEFAULT 'symbol-identity/v1',
      first_snapshot_id  TEXT,
      last_snapshot_id   TEXT,
      created_at         BIGINT NOT NULL,
      updated_at         BIGINT NOT NULL,
      PRIMARY KEY (repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(repo_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_symbols_lang ON symbols(repo_id, language)",
    """
    CREATE TABLE IF NOT EXISTS snapshot_symbols (
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      symbol_id   TEXT NOT NULL,
      kind        TEXT NOT NULL,
      name        TEXT NOT NULL,
      qname       TEXT NOT NULL,
      path        TEXT NOT NULL,
      start_line INT, start_col INT, end_line INT, end_col INT,
      meta        JSONB NOT NULL DEFAULT '{}',
      search_tsv  tsvector NOT NULL,
      PRIMARY KEY (repo_id, snapshot_id, symbol_id),
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id),
      FOREIGN KEY (repo_id, symbol_id)  REFERENCES symbols(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ss_path ON snapshot_symbols(repo_id, snapshot_id, path)",
    "CREATE INDEX IF NOT EXISTS idx_ss_kind ON snapshot_symbols(repo_id, snapshot_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_ss_qname ON snapshot_symbols(repo_id, snapshot_id, qname)",
    "CREATE INDEX IF NOT EXISTS idx_ss_legacy_lookup ON snapshot_symbols(repo_id, snapshot_id, kind, qname)",
    "ALTER TABLE symbols ADD COLUMN IF NOT EXISTS identity_schema_version TEXT NOT NULL DEFAULT 'symbol-identity/v1'",
    "ALTER TABLE symbols DROP CONSTRAINT IF EXISTS symbols_repo_id_kind_qname_key",
    "ALTER TABLE snapshot_symbols DROP CONSTRAINT IF EXISTS snapshot_symbols_repo_id_snapshot_id_kind_qname_key",
    "CREATE INDEX IF NOT EXISTS idx_ss_fts   ON snapshot_symbols USING GIN (search_tsv)",
    """
    CREATE TABLE IF NOT EXISTS snapshot_edges (
      repo_id       TEXT NOT NULL,
      snapshot_id   TEXT NOT NULL,
      id            TEXT NOT NULL,                  -- 'edge:{kind}:{src}:{dst}'
      kind          TEXT NOT NULL,
      src_symbol_id TEXT NOT NULL,
      dst_symbol_id TEXT NOT NULL,
      confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      meta          JSONB NOT NULL DEFAULT '{}',
      PRIMARY KEY (repo_id, snapshot_id, id),
      UNIQUE (repo_id, snapshot_id, kind, src_symbol_id, dst_symbol_id),
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id),
      FOREIGN KEY (repo_id, snapshot_id, src_symbol_id)
          REFERENCES snapshot_symbols(repo_id, snapshot_id, symbol_id),
      FOREIGN KEY (repo_id, snapshot_id, dst_symbol_id)
          REFERENCES snapshot_symbols(repo_id, snapshot_id, symbol_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_se_src  ON snapshot_edges(repo_id, snapshot_id, src_symbol_id)",
    "CREATE INDEX IF NOT EXISTS idx_se_dst  ON snapshot_edges(repo_id, snapshot_id, dst_symbol_id)",
    "CREATE INDEX IF NOT EXISTS idx_se_kind ON snapshot_edges(repo_id, snapshot_id, kind)",
    """
    CREATE TABLE IF NOT EXISTS symbol_evidence (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      symbol_id   TEXT NOT NULL,
      source      TEXT NOT NULL,
      confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      location    JSONB,
      payload     JSONB NOT NULL DEFAULT '{}',
      ts          BIGINT NOT NULL,
      FOREIGN KEY (repo_id, snapshot_id, symbol_id)
          REFERENCES snapshot_symbols(repo_id, snapshot_id, symbol_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sev_sym ON symbol_evidence(repo_id, snapshot_id, symbol_id)",
    """
    CREATE TABLE IF NOT EXISTS edge_evidence (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      edge_id     TEXT NOT NULL,
      source      TEXT NOT NULL,
      confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      location    JSONB,
      payload     JSONB NOT NULL DEFAULT '{}',
      ts          BIGINT NOT NULL,
      FOREIGN KEY (repo_id, snapshot_id, edge_id)
          REFERENCES snapshot_edges(repo_id, snapshot_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eev_edge ON edge_evidence(repo_id, snapshot_id, edge_id)",
    """
    CREATE TABLE IF NOT EXISTS canonical_support_receipts (
      repo_id TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      support_receipt_id TEXT NOT NULL,
      canonical_fact_id TEXT NOT NULL,
      canonical_fact_type TEXT NOT NULL CHECK (canonical_fact_type IN ('node','edge')),
      owner_path TEXT NOT NULL,
      receipt JSONB NOT NULL,
      PRIMARY KEY (repo_id, snapshot_id, support_receipt_id),
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_csr_fact ON canonical_support_receipts(repo_id, snapshot_id, canonical_fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_csr_owner ON canonical_support_receipts(repo_id, snapshot_id, owner_path)",
    """
    CREATE TABLE IF NOT EXISTS callsites (
      id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id            TEXT NOT NULL,
      snapshot_id        TEXT NOT NULL,
      file_path          TEXT NOT NULL,
      line               INT NOT NULL,
      col                INT,
      callee             TEXT NOT NULL,
      candidates         TEXT[] NOT NULL DEFAULT '{}',
      shape              JSONB,
      resolved_symbol_id TEXT,
      resolve_kind       TEXT,
      confidence         DOUBLE PRECISION,
      edge_id            TEXT,
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id),
      FOREIGN KEY (repo_id, resolved_symbol_id) REFERENCES symbols(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cs_file ON callsites(repo_id, snapshot_id, file_path)",
    """
    CREATE TABLE IF NOT EXISTS imports (
      id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id       TEXT NOT NULL,
      snapshot_id   TEXT NOT NULL,
      file_path     TEXT NOT NULL,
      line          INT,
      src_qname     TEXT NOT NULL,
      module_qname  TEXT NOT NULL,
      local         TEXT,
      only_names    TEXT[] NOT NULL DEFAULT '{}',
      meta          JSONB NOT NULL DEFAULT '{}',
      external      BOOLEAN NOT NULL DEFAULT FALSE,
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_imports_snap ON imports(repo_id, snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS includes (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      file_path   TEXT NOT NULL,
      line        INT,
      target      TEXT NOT NULL,
      meta        JSONB NOT NULL DEFAULT '{}',
      external    BOOLEAN NOT NULL DEFAULT FALSE,
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_includes_snap ON includes(repo_id, snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS pending_edges (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      kind        TEXT NOT NULL,
      file_path   TEXT NOT NULL,
      line        INT,
      src_mode    TEXT NOT NULL,
      src_value   TEXT NOT NULL,
      dst_mode    TEXT NOT NULL,
      dst_value   TEXT NOT NULL,
      confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
      meta        JSONB NOT NULL DEFAULT '{}',
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_snap ON pending_edges(repo_id, snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS unresolved (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      repo_id     TEXT NOT NULL,
      snapshot_id TEXT NOT NULL,
      kind        TEXT NOT NULL,
      detail      JSONB NOT NULL,
      ts          BIGINT NOT NULL,
      FOREIGN KEY (repo_id, snapshot_id) REFERENCES snapshots(repo_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_unres_snap ON unresolved(repo_id, snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS run_telemetry (
      id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      run_id   TEXT NOT NULL,
      repo_id  TEXT,
      language TEXT,
      phase    TEXT,
      key      TEXT NOT NULL,
      value    JSONB NOT NULL,
      ts       BIGINT NOT NULL
    )
    """,
    # ------------------------------------------------------------------
    # Architecture plane (v1.1-fix1: no independent designs table)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS arch_workspaces (
      id          UUID PRIMARY KEY,
      repo_id     TEXT NOT NULL REFERENCES repositories(id),
      name        TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      created_at  BIGINT NOT NULL,
      updated_at  BIGINT NOT NULL,
      meta        JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arch_models (
      id            UUID PRIMARY KEY,
      workspace_id  UUID NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
      kind          TEXT NOT NULL CHECK (kind IN ('as_is','proposal')),
      name          TEXT NOT NULL,
      description   TEXT NOT NULL DEFAULT '',
      status        TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','superseded')),
      parent_model_id UUID REFERENCES arch_models(id),
      base_evidence_snapshot_id TEXT,
      baseline_json JSONB,
      baseline_schema_version INT NOT NULL DEFAULT 1,
      created_at    BIGINT NOT NULL,
      updated_at    BIGINT NOT NULL,
      UNIQUE (workspace_id, id)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_models_as_is ON arch_models(workspace_id) WHERE kind = 'as_is'",
    """
    CREATE TABLE IF NOT EXISTS arch_components (
      id           UUID PRIMARY KEY,
      workspace_id UUID NOT NULL,
      model_id     UUID NOT NULL,
      origin_id    UUID,
      kind         TEXT NOT NULL
                   CHECK (kind IN ('component','subsystem','layer','service',
                                   'boundary','interface','datastore',
                                   'external_system','group')),
      name         TEXT NOT NULL,
      description  TEXT NOT NULL DEFAULT '',
      parent_id    UUID,
      sort_order   INT NOT NULL DEFAULT 0,
      created_at   BIGINT NOT NULL,
      updated_at   BIGINT NOT NULL,
      meta         JSONB NOT NULL DEFAULT '{}',
      UNIQUE (workspace_id, model_id, id),
      FOREIGN KEY (workspace_id, model_id)
          REFERENCES arch_models(workspace_id, id) ON DELETE CASCADE,
      FOREIGN KEY (workspace_id, model_id, parent_id)
          REFERENCES arch_components(workspace_id, model_id, id)
          ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ac_ws ON arch_components(workspace_id)",
    "CREATE INDEX IF NOT EXISTS idx_ac_model ON arch_components(model_id)",
    """
    CREATE TABLE IF NOT EXISTS arch_relations (
      id           UUID PRIMARY KEY,
      workspace_id UUID NOT NULL,
      model_id     UUID NOT NULL,
      kind         TEXT NOT NULL,
      src_id       UUID NOT NULL,
      dst_id       UUID NOT NULL,
      label        TEXT,
      created_at   BIGINT NOT NULL,
      updated_at   BIGINT NOT NULL,
      meta         JSONB NOT NULL DEFAULT '{}',
      UNIQUE (workspace_id, model_id, id),
      FOREIGN KEY (workspace_id, model_id)
          REFERENCES arch_models(workspace_id, id) ON DELETE CASCADE,
      FOREIGN KEY (workspace_id, model_id, src_id)
          REFERENCES arch_components(workspace_id, model_id, id)
          ON DELETE RESTRICT,
      FOREIGN KEY (workspace_id, model_id, dst_id)
          REFERENCES arch_components(workspace_id, model_id, id)
          ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ar_src ON arch_relations(workspace_id, model_id, src_id)",
    "CREATE INDEX IF NOT EXISTS idx_ar_dst ON arch_relations(workspace_id, model_id, dst_id)",
    """
    CREATE TABLE IF NOT EXISTS arch_mappings (
      id                   UUID PRIMARY KEY,
      workspace_id         UUID NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
      component_id         UUID NOT NULL REFERENCES arch_components(id) ON DELETE CASCADE,
      evidence_entity_type TEXT NOT NULL CHECK (evidence_entity_type IN ('node','edge')),
      evidence_entity_id   TEXT NOT NULL,
      evidence_snapshot_id TEXT,
      note                 TEXT NOT NULL DEFAULT '',
      created_at           BIGINT NOT NULL,
      updated_at           BIGINT NOT NULL,
      UNIQUE (component_id, evidence_entity_type, evidence_entity_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_am_comp ON arch_mappings(component_id)",
    """
    CREATE TABLE IF NOT EXISTS arch_annotations (
      id           UUID PRIMARY KEY,
      workspace_id UUID NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
      entity_type  TEXT NOT NULL CHECK (entity_type IN ('component','relation','mapping','workspace')),
      entity_id    TEXT NOT NULL,
      model_id     UUID REFERENCES arch_models(id) ON DELETE CASCADE,
      body         TEXT NOT NULL,
      created_at   BIGINT NOT NULL,
      updated_at   BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arch_layouts (
      workspace_id UUID NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
      model_id     UUID NOT NULL REFERENCES arch_models(id) ON DELETE CASCADE,
      layout       JSONB NOT NULL,
      updated_at   BIGINT NOT NULL,
      PRIMARY KEY (workspace_id, model_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_ui_state (
      workspace_id UUID PRIMARY KEY REFERENCES arch_workspaces(id) ON DELETE CASCADE,
      state        JSONB NOT NULL,
      updated_at   BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
      id          UUID PRIMARY KEY,
      kind        TEXT NOT NULL,
      repo_id     TEXT NOT NULL REFERENCES repositories(id),
      status      TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','done','failed')),
      progress    INT NOT NULL DEFAULT 0,
      result      JSONB,
      error       JSONB,
      created_at  BIGINT NOT NULL,
      finished_at BIGINT
    )
    """,
    # ------------------------------------------------------------------
    # Software Circuit plane (SPEC-P2 §3/§19): flow models, blocks, ports,
    # nets, bindings, layouts.  Composite FKs enforce "same FlowModel" for
    # Port->Block / Net endpoints / Block parent (renderer-neutral model;
    # hierarchy cycles validated at the service layer).
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS flow_models (
      id                   UUID PRIMARY KEY,
      workspace_id         UUID,
      architecture_model_id UUID,
      repo_id              TEXT NOT NULL REFERENCES repositories(id),
      name                 TEXT NOT NULL,
      scope_symbol_id      TEXT,
      root_block_id        UUID,
      snapshot_id          TEXT NOT NULL,
      status               TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active','archived')),
      version              INT NOT NULL DEFAULT 1,
      created_at           BIGINT NOT NULL,
      updated_at           BIGINT NOT NULL,
      meta                 JSONB NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_flow_models_repo ON flow_models(repo_id)",
    """
    CREATE TABLE IF NOT EXISTS flow_blocks (
      id              UUID PRIMARY KEY,
      flow_model_id   UUID NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
      parent_block_id UUID,
      kind            TEXT NOT NULL
                      CHECK (kind IN ('function','object','composite','proposed')),
      name            TEXT NOT NULL,
      state           TEXT NOT NULL
                      CHECK (state IN ('existing','modified','proposed')),
      code            TEXT,
      meta            JSONB NOT NULL DEFAULT '{}',
      created_at      BIGINT NOT NULL,
      updated_at      BIGINT NOT NULL,
      UNIQUE (flow_model_id, id),
      FOREIGN KEY (flow_model_id, parent_block_id)
          REFERENCES flow_blocks(flow_model_id, id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_flow_blocks_flow ON flow_blocks(flow_model_id)",
    """
    CREATE TABLE IF NOT EXISTS flow_ports (
      id             UUID PRIMARY KEY,
      flow_model_id  UUID NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
      block_id       UUID NOT NULL,
      name           TEXT NOT NULL,
      direction      TEXT NOT NULL CHECK (direction IN ('input','output')),
      semantic_kind  TEXT NOT NULL
                     CHECK (semantic_kind IN
                            ('data','control','event','error','resource')),
      code_type      TEXT,
      position_order INT NOT NULL DEFAULT 0,
      meta           JSONB NOT NULL DEFAULT '{}',
      created_at     BIGINT NOT NULL,
      UNIQUE (flow_model_id, id),
      FOREIGN KEY (flow_model_id, block_id)
          REFERENCES flow_blocks(flow_model_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_flow_ports_block ON flow_ports(block_id)",
    """
    CREATE TABLE IF NOT EXISTS flow_nets (
      id             UUID PRIMARY KEY,
      flow_model_id  UUID NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
      source_port_id UUID NOT NULL,
      target_port_id UUID NOT NULL,
      kind           TEXT NOT NULL DEFAULT 'control'
                     CHECK (kind IN ('data','control','event','error','resource')),
      label          TEXT,
      meta           JSONB NOT NULL DEFAULT '{}',
      created_at     BIGINT NOT NULL,
      FOREIGN KEY (flow_model_id, source_port_id)
          REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE,
      FOREIGN KEY (flow_model_id, target_port_id)
          REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_flow_nets_flow ON flow_nets(flow_model_id)",
    """
    CREATE TABLE IF NOT EXISTS flow_bindings (
      id                  UUID PRIMARY KEY,
      flow_model_id       UUID NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
      block_id            UUID NOT NULL,
      snapshot_id         TEXT NOT NULL,
      canonical_symbol_id TEXT NOT NULL,
      binding_kind        TEXT NOT NULL DEFAULT 'implementation'
                          CHECK (binding_kind IN ('implementation')),
      created_at          BIGINT NOT NULL,
      updated_at          BIGINT NOT NULL,
      UNIQUE (flow_model_id, block_id),
      FOREIGN KEY (flow_model_id, block_id)
          REFERENCES flow_blocks(flow_model_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flow_layouts (
      flow_model_id UUID PRIMARY KEY
                    REFERENCES flow_models(id) ON DELETE CASCADE,
      layout        JSONB NOT NULL,
      updated_at    BIGINT NOT NULL
    )
    """,
    # ------------------------------------------------------------------
    # DESIGN-LIFECYCLE0: one aggregate + immutable design revisions and
    # command receipts.  Design references are JSONB payloads, never FKs to
    # canonical evidence and never canonical candidates themselves.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS design_changes (
      id TEXT PRIMARY KEY,
      repo_id TEXT NOT NULL REFERENCES repositories(id),
      base_canonical_revision TEXT NOT NULL,
      state TEXT NOT NULL,
      version INT NOT NULL,
      aggregate_json JSONB NOT NULL,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_design_changes_repo"
    " ON design_changes(repo_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS design_revisions (
      id TEXT PRIMARY KEY,
      change_id TEXT NOT NULL REFERENCES design_changes(id),
      revision_json JSONB NOT NULL,
      digest TEXT NOT NULL,
      created_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS design_lifecycle_receipts (
      id TEXT PRIMARY KEY,
      change_id TEXT NOT NULL REFERENCES design_changes(id),
      sequence INT NOT NULL,
      command TEXT NOT NULL,
      actor TEXT NOT NULL,
      state_before TEXT,
      state_after TEXT NOT NULL,
      payload_json JSONB NOT NULL,
      digest TEXT NOT NULL,
      created_at BIGINT NOT NULL,
      UNIQUE(change_id, sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS design_coverage_certificates (
      id TEXT PRIMARY KEY,
      repo_id TEXT NOT NULL,
      canonical_revision TEXT NOT NULL,
      subject TEXT NOT NULL,
      relation_kind TEXT NOT NULL,
      payload_json JSONB NOT NULL,
      created_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_design_coverage_lookup"
    " ON design_coverage_certificates(repo_id, canonical_revision, subject,"
    " relation_kind, created_at)",
    """
    CREATE TABLE IF NOT EXISTS design_test_receipts (
      id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
      payload_json JSONB NOT NULL, digest TEXT NOT NULL, created_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS design_approval_receipts (
      id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
      payload_json JSONB NOT NULL, digest TEXT NOT NULL, created_at BIGINT NOT NULL
    )
    """,
    """
    CREATE OR REPLACE FUNCTION reject_design_immutable_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'design lifecycle immutable row';
    END $$
    """,
    "DROP TRIGGER IF EXISTS design_revisions_no_update ON design_revisions",
    "CREATE TRIGGER design_revisions_no_update BEFORE UPDATE OR DELETE"
    " ON design_revisions FOR EACH ROW"
    " EXECUTE FUNCTION reject_design_immutable_mutation()",
    "DROP TRIGGER IF EXISTS design_receipts_no_update ON design_lifecycle_receipts",
    "CREATE TRIGGER design_receipts_no_update BEFORE UPDATE OR DELETE"
    " ON design_lifecycle_receipts FOR EACH ROW"
    " EXECUTE FUNCTION reject_design_immutable_mutation()",
    "DROP TRIGGER IF EXISTS design_coverage_no_update ON design_coverage_certificates",
    "CREATE TRIGGER design_coverage_no_update BEFORE UPDATE OR DELETE"
    " ON design_coverage_certificates FOR EACH ROW"
    " EXECUTE FUNCTION reject_design_immutable_mutation()",
    "DROP TRIGGER IF EXISTS design_test_no_update ON design_test_receipts",
    "CREATE TRIGGER design_test_no_update BEFORE UPDATE OR DELETE"
    " ON design_test_receipts FOR EACH ROW"
    " EXECUTE FUNCTION reject_design_immutable_mutation()",
    "DROP TRIGGER IF EXISTS design_approval_no_update ON design_approval_receipts",
    "CREATE TRIGGER design_approval_no_update BEFORE UPDATE OR DELETE"
    " ON design_approval_receipts FOR EACH ROW"
    " EXECUTE FUNCTION reject_design_immutable_mutation()",
]
# fmt: on

# pg_trgm-backed substring indexes (performance only).  pg_trgm ships with
# standard PostgreSQL distributions (contrib) but is absent from the embedded
# binaries used for dev tests — create_all() applies these only when the
# extension is available (SPEC-P1 §6.1-4; correctness never depends on them).
TRGM_INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_ss_trgm_q ON snapshot_symbols"
    " USING GIN (qname public.gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_ss_trgm_n ON snapshot_symbols"
    " USING GIN (name public.gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_ss_trgm_p ON snapshot_symbols"
    " USING GIN (path public.gin_trgm_ops)",
]

# Drop order (reverse dependency order) for alembic downgrade.
ALL_TABLES: list[str] = [
    "design_lifecycle_receipts",
    "design_coverage_certificates",
    "design_test_receipts",
    "design_approval_receipts",
    "design_revisions",
    "design_changes",
    "flow_layouts",
    "flow_bindings",
    "flow_nets",
    "flow_ports",
    "flow_blocks",
    "flow_models",
    "jobs",
    "workspace_ui_state",
    "arch_layouts",
    "arch_annotations",
    "arch_mappings",
    "arch_relations",
    "arch_components",
    "arch_models",
    "arch_workspaces",
    "run_telemetry",
    "unresolved",
    "pending_edges",
    "includes",
    "imports",
    "callsites",
    "edge_evidence",
    "symbol_evidence",
    "snapshot_edges",
    "snapshot_symbols",
    "symbols",
    "files",
    "snapshots",
    "repositories",
]


def create_all(conn) -> None:
    """Execute the full baseline DDL (idempotent) on the given connection.

    `pg_trgm` is applied when available (standard PG installs); on minimal
    embedded builds it is skipped together with the trigram GIN indexes —
    search correctness is unchanged, only substring acceleration is lost.
    """
    has_trgm = False
    try:
        # Explicit `SCHEMA public`: callers may run with an isolated
        # search_path (e.g. the per-test schemas in tests/conftest.py), and
        # gin_trgm_ops must live where the GIN indexes can resolve it.
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")
        has_trgm = True
    except Exception:  # noqa: BLE001 - minimal builds lack contrib
        pass
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    if has_trgm:
        for stmt in TRGM_INDEX_STATEMENTS:
            conn.execute(stmt)
