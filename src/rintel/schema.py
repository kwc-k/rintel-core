"""SQLite DDL for the canonical evidence graph (spec §2, §7-§9, §26, §40)."""
from __future__ import annotations

DDL = """
CREATE TABLE IF NOT EXISTS repos (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  parent_id TEXT,
  commit_sha TEXT,
  publication_status TEXT NOT NULL DEFAULT 'published',
  created_at INTEGER NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_snapshots_repo ON snapshots(repo_id);

-- per-file index state: hash + parser/indexer versions (spec §26)
CREATE TABLE IF NOT EXISTS files (
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  hash TEXT NOT NULL,
  size INTEGER NOT NULL,
  parser_version TEXT,
  indexer_version TEXT,
  semantic_digest TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  last_indexed_at INTEGER,
  PRIMARY KEY (repo_id, path)
);

CREATE TABLE IF NOT EXISTS nodes (
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  qname TEXT NOT NULL,
  language TEXT NOT NULL,
  identity_schema_version TEXT NOT NULL DEFAULT 'symbol-identity/v1',
  path TEXT NOT NULL,
  start_line INTEGER, start_col INTEGER, end_line INTEGER, end_col INTEGER,
  meta_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (repo_id, snapshot_id, id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_snap  ON nodes(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_nodes_qname ON nodes(repo_id, qname);
CREATE INDEX IF NOT EXISTS idx_nodes_kind  ON nodes(repo_id, kind);
CREATE INDEX IF NOT EXISTS idx_nodes_path  ON nodes(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_nodes_lang  ON nodes(repo_id, language);
CREATE INDEX IF NOT EXISTS idx_nodes_legacy_lookup ON nodes
  (repo_id, snapshot_id, kind, qname);

CREATE TABLE IF NOT EXISTS edges (
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  id TEXT NOT NULL,
  kind TEXT NOT NULL,
  src_id TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  meta_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (repo_id, snapshot_id, id),
  UNIQUE(repo_id, snapshot_id, kind, src_id, dst_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_snap ON edges(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(repo_id, kind);

-- every non-trivial relation must be able to carry evidence metadata
-- (source/confidence/location/snapshot/timestamp) — spec §9.
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,          -- 'node' | 'edge'
  entity_id TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  location_json TEXT,
  ts INTEGER NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evidence_ent ON evidence(entity_type, entity_id);

-- Admission-derived provenance is separate from legacy extractor evidence.
-- Only staging revisions may be changed; published rows are immutable.
CREATE TABLE IF NOT EXISTS canonical_support_receipts (
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  support_receipt_id TEXT NOT NULL,
  canonical_fact_id TEXT NOT NULL,
  canonical_fact_type TEXT NOT NULL CHECK (canonical_fact_type IN ('node','edge')),
  owner_path TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  PRIMARY KEY (repo_id, snapshot_id, support_receipt_id),
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);
CREATE INDEX IF NOT EXISTS idx_csr_fact ON canonical_support_receipts
  (repo_id, snapshot_id, canonical_fact_id);
CREATE INDEX IF NOT EXISTS idx_csr_owner ON canonical_support_receipts
  (repo_id, snapshot_id, owner_path);

-- call sites kept for re-resolution (incremental index) and telemetry
CREATE TABLE IF NOT EXISTS callsites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER NOT NULL,
  col INTEGER,
  callee TEXT NOT NULL,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  shape TEXT,                         -- FAC-EQ0 call shape {"form": "stmt"|"expr", "args": n}
  resolved_node_id TEXT,
  resolve_kind TEXT,                  -- same_file|imported|global_unique|self|constructor|module|unresolved
  confidence REAL,
  edge_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_callsites_file ON callsites(repo_id, file_path);

-- recorded non-fatal limitations (unsupported relation / unresolved dynamic
-- call / partial data-flow) — spec §32: never silently fabricate.
CREATE TABLE IF NOT EXISTS unresolved (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  ts INTEGER NOT NULL
);

-- import bindings + include bindings: kept per snapshot so resolution-derived
-- edges (IMPORTS / REFERENCES / INCLUDES) can be recomputed on incremental
-- updates without reparsing unchanged files
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER,
  src_qname TEXT NOT NULL,
  module_qname TEXT NOT NULL,
  local TEXT,
  only_names_json TEXT NOT NULL DEFAULT '[]',
  meta_json TEXT NOT NULL DEFAULT '{}',
  external INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_imports_snap ON imports(repo_id, snapshot_id);

CREATE TABLE IF NOT EXISTS includes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER,
  target TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  external INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_includes_snap ON includes(repo_id, snapshot_id);

-- adapter-emitted edges whose endpoints are symbolic refs; resolved by the
-- resolution pass after all files of the snapshot are inserted, and
-- re-resolved on every snapshot so cross-file identity never goes stale
CREATE TABLE IF NOT EXISTS pending_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER,
  src_mode TEXT NOT NULL,
  src_value TEXT NOT NULL,
  dst_mode TEXT NOT NULL,
  dst_value TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pending_snap ON pending_edges(repo_id, snapshot_id);

-- per-run benchmark telemetry (spec §43.7 raw result bundle)
CREATE TABLE IF NOT EXISTS run_telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  repo_id TEXT,
  language TEXT,
  phase TEXT,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  ts INTEGER NOT NULL
);

-- full-text search over node identity (spec §25 lexical search)
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  name, qname, path,
  content='nodes', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, name, qname, path)
  VALUES (new.rowid, new.name, new.qname, new.path);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, name, qname, path)
  VALUES ('delete', old.rowid, old.name, old.qname, old.path);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, name, qname, path)
  VALUES ('delete', old.rowid, old.name, old.qname, old.path);
  INSERT INTO nodes_fts(rowid, name, qname, path)
  VALUES (new.rowid, new.name, new.qname, new.path);
END;
"""

# Architecture plane (SPEC-P1 §6.2, S3 slice): SQLite mirror of the PG DDL —
# TEXT ids + *_json payloads, same composite-FK model isolation (R1), same
# partial-unique AS-IS per workspace (fix1), same RESTRICT delete semantics
# (R5). Requires PRAGMA foreign_keys=ON (set by Database.__init__).
ARCH_DDL = """
CREATE TABLE IF NOT EXISTS arch_workspaces (
  id          TEXT PRIMARY KEY,
  repo_id     TEXT NOT NULL REFERENCES repos(id),
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  meta_json   TEXT NOT NULL DEFAULT '{}'
);

-- R1: every arch asset explicitly belongs to an arch_models row (as_is |
-- proposal); cross-model references are rejected by composite FKs.
CREATE TABLE IF NOT EXISTS arch_models (
  id            TEXT PRIMARY KEY,
  workspace_id  TEXT NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('as_is','proposal')),
  name          TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','superseded')),
  parent_model_id TEXT REFERENCES arch_models(id),
  base_evidence_snapshot_id TEXT,
  baseline_json TEXT,
  baseline_schema_version INTEGER NOT NULL DEFAULT 1,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  UNIQUE (workspace_id, id)
);
-- fix1: at most one AS-IS model per workspace
CREATE UNIQUE INDEX IF NOT EXISTS uq_models_as_is
  ON arch_models(workspace_id) WHERE kind = 'as_is';

CREATE TABLE IF NOT EXISTS arch_components (
  id           TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  model_id     TEXT NOT NULL,
  origin_id    TEXT,
  kind         TEXT NOT NULL
               CHECK (kind IN ('component','subsystem','layer','service',
                               'boundary','interface','datastore',
                               'external_system','group')),
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  parent_id    TEXT,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  meta_json    TEXT NOT NULL DEFAULT '{}',
  UNIQUE (workspace_id, model_id, id),
  FOREIGN KEY (workspace_id, model_id)
      REFERENCES arch_models(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, model_id, parent_id)
      REFERENCES arch_components(workspace_id, model_id, id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_ac_ws ON arch_components(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ac_model ON arch_components(model_id);

CREATE TABLE IF NOT EXISTS arch_relations (
  id           TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  model_id     TEXT NOT NULL,
  kind         TEXT NOT NULL,       -- DEPENDS_ON|USES|CALLS|DATA_FLOW|PROVIDES|CONTAINS
  src_id       TEXT NOT NULL,
  dst_id       TEXT NOT NULL,
  label        TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  meta_json    TEXT NOT NULL DEFAULT '{}',
  UNIQUE (workspace_id, model_id, id),
  FOREIGN KEY (workspace_id, model_id)
      REFERENCES arch_models(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, model_id, src_id)
      REFERENCES arch_components(workspace_id, model_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, model_id, dst_id)
      REFERENCES arch_components(workspace_id, model_id, id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_ar_src ON arch_relations(workspace_id, model_id, src_id);
CREATE INDEX IF NOT EXISTS idx_ar_dst ON arch_relations(workspace_id, model_id, dst_id);

CREATE TABLE IF NOT EXISTS arch_mappings (      -- Architecture <-> Evidence
  id                   TEXT PRIMARY KEY,
  workspace_id         TEXT NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
  component_id         TEXT NOT NULL REFERENCES arch_components(id) ON DELETE CASCADE,
  evidence_entity_type TEXT NOT NULL CHECK (evidence_entity_type IN ('node','edge')),
  evidence_entity_id   TEXT NOT NULL,           -- canonical 'node:...' / 'edge:...'
  evidence_snapshot_id TEXT,
  note                 TEXT NOT NULL DEFAULT '',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL,
  UNIQUE (component_id, evidence_entity_type, evidence_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_am_comp ON arch_mappings(component_id);

CREATE TABLE IF NOT EXISTS arch_layouts (
  workspace_id TEXT NOT NULL REFERENCES arch_workspaces(id) ON DELETE CASCADE,
  model_id     TEXT NOT NULL REFERENCES arch_models(id) ON DELETE CASCADE,
  layout_json  TEXT NOT NULL,
  updated_at   INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, model_id)
);

CREATE TABLE IF NOT EXISTS workspace_ui_state (
  workspace_id TEXT PRIMARY KEY REFERENCES arch_workspaces(id) ON DELETE CASCADE,
  state_json   TEXT NOT NULL,
  updated_at   INTEGER NOT NULL
);
"""

# Software Circuit plane (SPEC-P2 §3 / §19): renderer-neutral netlist.
# Mirror of the PG DDL.  FK rules (§19):
#   Port -> Block; Net source/target -> same FlowModel; Block parent -> same
#   FlowModel (composite FKs, enforced); Binding -> Block.
# Hierarchy cycles (Block parent, composite membership) are validated at the
# service layer (no recursive DB triggers, §19).
FLOW_DDL = """
CREATE TABLE IF NOT EXISTS flow_models (
  id                     TEXT PRIMARY KEY,
  workspace_id           TEXT,
  architecture_model_id  TEXT,
  repo_id                TEXT NOT NULL REFERENCES repos(id),
  name                   TEXT NOT NULL,
  scope_symbol_id        TEXT,
  root_block_id          TEXT,
  snapshot_id            TEXT NOT NULL,
  status                 TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','archived')),
  version                INTEGER NOT NULL DEFAULT 1,
  created_at             INTEGER NOT NULL,
  updated_at             INTEGER NOT NULL,
  meta_json              TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS flow_blocks (
  id             TEXT PRIMARY KEY,
  flow_model_id  TEXT NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
  parent_block_id TEXT,
  kind           TEXT NOT NULL
                 CHECK (kind IN ('function','object','composite','proposed')),
  name           TEXT NOT NULL,
  state          TEXT NOT NULL
                 CHECK (state IN ('existing','modified','proposed')),
  code           TEXT,
  meta_json      TEXT NOT NULL DEFAULT '{}',
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  UNIQUE (flow_model_id, id),
  FOREIGN KEY (flow_model_id, parent_block_id)
      REFERENCES flow_blocks(flow_model_id, id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_flow_blocks_flow ON flow_blocks(flow_model_id);

CREATE TABLE IF NOT EXISTS flow_ports (
  id             TEXT PRIMARY KEY,
  flow_model_id  TEXT NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
  block_id       TEXT NOT NULL,
  name           TEXT NOT NULL,
  direction      TEXT NOT NULL
                 CHECK (direction IN ('input','output')),
  semantic_kind  TEXT NOT NULL
                 CHECK (semantic_kind IN
                        ('data','control','event','error','resource')),
  code_type      TEXT,
  position_order INTEGER NOT NULL DEFAULT 0,
  meta_json      TEXT NOT NULL DEFAULT '{}',
  created_at     INTEGER NOT NULL,
  UNIQUE (flow_model_id, id),
  FOREIGN KEY (flow_model_id, block_id)
      REFERENCES flow_blocks(flow_model_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_flow_ports_block ON flow_ports(block_id);

CREATE TABLE IF NOT EXISTS flow_nets (
  id              TEXT PRIMARY KEY,
  flow_model_id   TEXT NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
  source_port_id  TEXT NOT NULL,
  target_port_id  TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'control'
                  CHECK (kind IN ('data','control','event','error','resource')),
  label           TEXT,
  meta_json       TEXT NOT NULL DEFAULT '{}',
  created_at      INTEGER NOT NULL,
  FOREIGN KEY (flow_model_id, source_port_id)
      REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE,
  FOREIGN KEY (flow_model_id, target_port_id)
      REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_flow_nets_flow ON flow_nets(flow_model_id);

CREATE TABLE IF NOT EXISTS flow_bindings (
  id                   TEXT PRIMARY KEY,
  flow_model_id        TEXT NOT NULL REFERENCES flow_models(id) ON DELETE CASCADE,
  block_id             TEXT NOT NULL,
  snapshot_id          TEXT NOT NULL,
  canonical_symbol_id  TEXT NOT NULL,
  binding_kind         TEXT NOT NULL DEFAULT 'implementation'
                       CHECK (binding_kind IN ('implementation')),
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL,
  UNIQUE (flow_model_id, block_id),
  FOREIGN KEY (flow_model_id, block_id)
      REFERENCES flow_blocks(flow_model_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS flow_layouts (
  flow_model_id TEXT PRIMARY KEY REFERENCES flow_models(id) ON DELETE CASCADE,
  layout_json   TEXT NOT NULL,
  updated_at    INTEGER NOT NULL
);
"""
