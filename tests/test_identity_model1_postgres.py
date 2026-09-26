"""Live PostgreSQL parity for versioned published graph identity."""
from __future__ import annotations

import os
import importlib
from pathlib import Path
import uuid

import pytest
from psycopg.types.json import Jsonb

from rintel.indexer import Indexer
from rintel.mcp import published
from rintel.provider_protocol.host import _canonical_entity
from rintel.pg.pgschema import create_all
from rintel.pg.pgstore import PgStore


@pytest.fixture
def pg_store():
    dsn = os.environ.get("RINTEL_TEST_PG_DSN")
    if not dsn:
        pytest.skip("RINTEL_TEST_PG_DSN not set; PostgreSQL parity NOT_RUN")
    import psycopg

    schema = "identity_model1_" + uuid.uuid4().hex[:12]
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    store = None
    try:
        admin.execute(f'SET search_path TO "{schema}"')
        create_all(admin)
        store = PgStore(dsn, schema=schema)
        yield store
    finally:
        if store is not None:
            store.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_three_language_collision_and_legacy_ambiguity_pg(
        pg_store: PgStore, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "native.c").write_text(
        "int foo(void) { return 1; }\nint caller(void) { return foo(); }\n")
    (root / "module.py").write_text("def foo():\n    return 2\n")
    (root / "numeric.f90").write_text(
        "integer function foo()\nfoo = 3\nend function foo\n")
    result = Indexer(pg_store, root, repo_id="identity-pg").index()
    rows = [row for row in pg_store.all_nodes("identity-pg", result.snapshot_id)
            if row["kind"] == "FUNCTION" and row["name"] == "foo"]
    assert {row["language"] for row in rows} == {"c", "python", "fortran"}
    assert len({row["id"] for row in rows}) == 3
    assert {row["identity_schema_version"] for row in rows} == {
        "symbol-identity/v2"}
    assert pg_store.node_by_id("identity-pg", result.snapshot_id,
                               "node:FUNCTION:foo") is None
    assert len(pg_store.nodes_by_legacy_id("identity-pg", result.snapshot_id,
                                          "node:FUNCTION:foo")) == 2
    caller = next(row for row in pg_store.all_nodes(
        "identity-pg", result.snapshot_id) if row["name"] == "caller")
    calls = [edge for edge in pg_store.edges_for_node(
        "identity-pg", result.snapshot_id, caller["id"])
        if edge["kind"] == "CALLS" and edge["src_id"] == caller["id"]]
    assert [edge["dst_id"] for edge in calls] == [next(
        row["id"] for row in rows if row["language"] == "c")]
    assert _canonical_entity({
        "local_id": "clang:foo", "kind": "FUNCTION", "name": "foo",
        "qualified_name": "foo", "path": "native.c", "language": "c",
    }) == next(row["id"] for row in rows if row["language"] == "c")


def test_pg_v1_constraint_migration_is_repeatable_and_preserves_old_row(
        pg_store: PgStore, monkeypatch) -> None:
    conn = pg_store.conn
    pg_store.upsert_repo("identity-pg", "/historical/source")
    old_sid = pg_store.new_snapshot("identity-pg", None)
    pg_store.publish_snapshot("identity-pg", old_sid)
    conn.execute(
        "INSERT INTO symbols(repo_id,id,kind,name,qname,language,"
        "first_snapshot_id,last_snapshot_id,created_at,updated_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        ("identity-pg", "node:FUNCTION:unique", "FUNCTION", "unique",
         "unique", "c", old_sid, old_sid, 1, 1))
    conn.execute(
        "INSERT INTO snapshot_symbols(repo_id,snapshot_id,symbol_id,kind,"
        "name,qname,path,meta,search_tsv) VALUES "
        "(%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s))",
        ("identity-pg", old_sid, "node:FUNCTION:unique", "FUNCTION",
         "unique", "unique", "native.c", Jsonb({}), "unique"))
    conn.execute("ALTER TABLE symbols ADD CONSTRAINT "
                 "symbols_repo_id_kind_qname_key UNIQUE (repo_id,kind,qname)")
    conn.execute("ALTER TABLE snapshot_symbols ADD CONSTRAINT "
                 "snapshot_symbols_repo_id_snapshot_id_kind_qname_key "
                 "UNIQUE (repo_id,snapshot_id,kind,qname)")
    migration = importlib.import_module(
        "rintel.pg.alembic.versions.0007_symbol_identity_v2")

    class MigrationOp:
        @staticmethod
        def execute(statement):
            conn.execute(statement)

    monkeypatch.setattr(migration, "op", MigrationOp)
    migration.upgrade()
    migration.upgrade()
    old = pg_store.node_by_id("identity-pg", old_sid,
                              "node:FUNCTION:unique")
    assert old["identity_schema_version"] == "symbol-identity/v1"
    assert old["id"] == "node:FUNCTION:unique"
    constraints = conn.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid IN "
        "('symbols'::regclass,'snapshot_symbols'::regclass)").fetchall()
    assert not {row["conname"] for row in constraints} & {
        "symbols_repo_id_kind_qname_key",
        "snapshot_symbols_repo_id_snapshot_id_kind_qname_key"}


def test_pg_duplicate_definition_fails_without_advancing_current(
        pg_store: PgStore, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.c").write_text("int foo(void) { return 1; }\n")
    first = Indexer(pg_store, root, repo_id="identity-pg").index()
    (root / "b.c").write_text("int foo(void) { return 2; }\n")
    with pytest.raises(ValueError, match="IDENTITY_COLLISION"):
        Indexer(pg_store, root, repo_id="identity-pg").index()
    assert pg_store.current_snapshot("identity-pg") == first.snapshot_id


def test_pg_public_query_propagates_v2_and_rejects_ambiguous_alias(
        pg_store: PgStore, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "native.c").write_text(
        "int target(void) { return 1; }\n"
        "int unique(void) { return target(); }\n", encoding="utf-8")
    (root / "numeric.f90").write_text(
        "integer function target()\ntarget = 2\nend function target\n",
        encoding="utf-8")
    sid = Indexer(pg_store, root, repo_id="identity-pg").index().snapshot_id
    nodes = pg_store.all_nodes("identity-pg", sid)
    caller = next(node for node in nodes if node["name"] == "unique")
    target = next(node for node in nodes if node["name"] == "target"
                  and node["language"] == "c")
    topology = published.topology(pg_store, {
        "repo_id": "identity-pg", "root": "node:FUNCTION:unique",
        "direction": "out", "relations": ["CALL"]})
    assert topology["root"] == caller["id"]
    assert {edge["target"] for edge in topology["edges"]} == {target["id"]}
    path = published.find_path(pg_store, {
        "repo_id": "identity-pg", "source": "node:FUNCTION:unique",
        "target": target["id"], "relations": ["CALL"]})
    assert path["verdict"] == "FOUND"
    ambiguous = published.topology(pg_store, {
        "repo_id": "identity-pg", "root": "node:FUNCTION:target"})
    assert ambiguous["error"]["code"] == "AMBIGUOUS_LEGACY_ID"
    assert len(ambiguous["error"]["candidates"]) == 2
