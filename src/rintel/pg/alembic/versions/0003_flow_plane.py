"""P2-FLOW0: software circuit plane (flow_models / flow_blocks / flow_ports /
flow_nets / flow_bindings / flow_layouts)."""
from alembic import op

revision = "0003_flow_plane"
down_revision = "0002_eq0_callsite_shape"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE flow_models (
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
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_flow_models_repo"
               " ON flow_models(repo_id)")
    op.execute("""
        CREATE TABLE flow_blocks (
          id              UUID PRIMARY KEY,
          flow_model_id   UUID NOT NULL
                          REFERENCES flow_models(id) ON DELETE CASCADE,
          parent_block_id UUID,
          kind            TEXT NOT NULL
                          CHECK (kind IN
                                 ('function','object','composite','proposed')),
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
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_flow_blocks_flow"
               " ON flow_blocks(flow_model_id)")
    op.execute("""
        CREATE TABLE flow_ports (
          id             UUID PRIMARY KEY,
          flow_model_id  UUID NOT NULL
                         REFERENCES flow_models(id) ON DELETE CASCADE,
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
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_flow_ports_block"
               " ON flow_ports(block_id)")
    op.execute("""
        CREATE TABLE flow_nets (
          id             UUID PRIMARY KEY,
          flow_model_id  UUID NOT NULL
                         REFERENCES flow_models(id) ON DELETE CASCADE,
          source_port_id UUID NOT NULL,
          target_port_id UUID NOT NULL,
          kind           TEXT NOT NULL DEFAULT 'control'
                         CHECK (kind IN
                                ('data','control','event','error','resource')),
          label          TEXT,
          meta           JSONB NOT NULL DEFAULT '{}',
          created_at     BIGINT NOT NULL,
          FOREIGN KEY (flow_model_id, source_port_id)
              REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE,
          FOREIGN KEY (flow_model_id, target_port_id)
              REFERENCES flow_ports(flow_model_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_flow_nets_flow"
               " ON flow_nets(flow_model_id)")
    op.execute("""
        CREATE TABLE flow_bindings (
          id                  UUID PRIMARY KEY,
          flow_model_id       UUID NOT NULL
                              REFERENCES flow_models(id) ON DELETE CASCADE,
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
    """)
    op.execute("""
        CREATE TABLE flow_layouts (
          flow_model_id UUID PRIMARY KEY
                        REFERENCES flow_models(id) ON DELETE CASCADE,
          layout        JSONB NOT NULL,
          updated_at    BIGINT NOT NULL
        )
    """)


def downgrade() -> None:
    for table in ("flow_layouts", "flow_bindings", "flow_nets", "flow_ports",
                  "flow_blocks", "flow_models"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
