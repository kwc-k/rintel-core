"""Versioned symbol identity contracts; no truth or authority promotion."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from rintel.db import Database
from rintel.indexer import Indexer
from rintel.mcp import published as published_store


def _put(root: Path, files: dict[str, str]) -> None:
    for path, contents in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


def test_same_spelling_three_languages_have_distinct_stable_ids_and_call_target(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _put(root, {
        "native.c": "int foo(void) { return 1; }\nint caller(void) { return foo(); }\n",
        "module.py": "def foo():\n    return 2\n",
        "numeric.f90": "integer function foo()\nfoo = 3\nend function foo\n",
    })
    db = Database(tmp_path / "identity.sqlite")
    try:
        first = Indexer(db, root, repo_id="identity").index()
        rows = [n for n in db.all_nodes("identity", first.snapshot_id)
                if n["kind"] == "FUNCTION" and n["name"] == "foo"]
        assert {n["language"] for n in rows} == {"c", "python", "fortran"}
        assert len({n["id"] for n in rows}) == 3
        assert all(n["id"].startswith("node:FUNCTION:v2:") for n in rows)
        matches = published_store.search_symbols(
            db, {"query": "foo", "repo_id": "identity", "limit": 5})
        assert {row["canonical_id"] for row in matches
                if row["kind"] == "FUNCTION"} == {row["id"] for row in rows}
        assert db.node_by_id("identity", first.snapshot_id,
                             "node:FUNCTION:foo") is None
        old = published_store.get_symbol(db, "node:FUNCTION:foo", "identity")
        assert old["error"]["code"] == "AMBIGUOUS_LEGACY_ID"
        c_caller = next(n for n in db.all_nodes("identity", first.snapshot_id)
                        if n["name"] == "caller")
        calls = [e for e in db.edges_for_node("identity", first.snapshot_id,
                                               c_caller["id"])
                 if e["kind"] == "CALLS" and e["src_id"] == c_caller["id"]]
        assert [e["dst_id"] for e in calls] == [next(
            n["id"] for n in rows if n["language"] == "c")]
    finally:
        db.close()


def test_stable_id_survives_new_snapshot_but_occurrence_is_scoped(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _put(root, {"module.py": "def foo():\n    return 1\n"})
    db = Database(tmp_path / "identity.sqlite")
    try:
        first = Indexer(db, root, repo_id="identity").index()
        old = next(n for n in db.all_nodes("identity", first.snapshot_id)
                   if n["name"] == "foo")
        _put(root, {"module.py": "def foo():\n    return helper()\n\ndef helper():\n    return 2\n"})
        second = Indexer(db, root, repo_id="identity").index()
        new = next(n for n in db.all_nodes("identity", second.snapshot_id)
                   if n["name"] == "foo")
        assert second.snapshot_id != first.snapshot_id
        assert old["id"] == new["id"]
        assert old["snapshot_id"] != new["snapshot_id"]
        assert db.node_by_id("identity", first.snapshot_id, old["id"])
        assert db.node_by_id("identity", second.snapshot_id, new["id"])
        assert db.current_snapshot("identity") == second.snapshot_id
    finally:
        db.close()


def test_unique_legacy_id_can_resolve_without_rewriting_old_evidence(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _put(root, {"native.c": "int unique(void) { return 1; }\n"})
    db = Database(tmp_path / "identity.sqlite")
    try:
        result = Indexer(db, root, repo_id="identity").index()
        node = db.node_by_id("identity", result.snapshot_id,
                             "node:FUNCTION:unique")
        assert node is not None
        assert node["id"].startswith("node:FUNCTION:v2:")
        assert db.node_by_id("identity", result.snapshot_id,
                             node["id"]) == node
    finally:
        db.close()


def test_duplicate_external_definition_fails_closed_without_current_move(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _put(root, {"a.c": "int foo(void) { return 1; }\n"})
    db = Database(tmp_path / "identity.sqlite")
    try:
        first = Indexer(db, root, repo_id="identity").index()
        _put(root, {"b.c": "int foo(void) { return 2; }\n"})
        with pytest.raises(ValueError, match="IDENTITY_COLLISION"):
            Indexer(db, root, repo_id="identity").index()
        assert db.current_snapshot("identity") == first.snapshot_id
    finally:
        db.close()


def test_v1_published_snapshot_is_preserved_during_v2_reindex(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _put(root, {"native.c": "int unique(void) { return 1; }\n"})
    db = Database(tmp_path / "identity.sqlite")
    try:
        db.upsert_repo("identity", str(root))
        old_sid = db.new_snapshot("identity", None, meta={
            "analysis_identity": {"semantic_identity_schema_version": "1"}})
        db.conn.execute(
            "INSERT INTO nodes(repo_id,snapshot_id,id,kind,name,qname,language,"
            "path,meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
            ("identity", old_sid, "node:FUNCTION:unique", "FUNCTION",
             "unique", "unique", "c", "native.c", '{"defined":true}'))
        db.publish_snapshot("identity", old_sid)
        db.commit()
        result = Indexer(db, root, repo_id="identity").index()
        assert result.snapshot_id != old_sid
        assert result.incremental is False
        old = db.node_by_id("identity", old_sid, "node:FUNCTION:unique")
        new = db.node_by_id("identity", result.snapshot_id,
                            "node:FUNCTION:unique")
        assert old["id"] == "node:FUNCTION:unique"
        assert old["identity_schema_version"] == "symbol-identity/v1"
        assert new["id"].startswith("node:FUNCTION:v2:")
        assert new["identity_schema_version"] == "symbol-identity/v2"
        assert db.current_snapshot("identity") == result.snapshot_id
    finally:
        db.close()


def test_sqlite_v1_schema_migration_is_additive_and_repeatable(
        tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE nodes (
        repo_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, id TEXT NOT NULL,
        kind TEXT NOT NULL, name TEXT NOT NULL, qname TEXT NOT NULL,
        language TEXT NOT NULL, path TEXT NOT NULL,
        start_line INTEGER, start_col INTEGER, end_line INTEGER, end_col INTEGER,
        meta_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (repo_id, snapshot_id, id))""")
    old.execute("INSERT INTO nodes(repo_id,snapshot_id,id,kind,name,qname,"
                "language,path) VALUES (?,?,?,?,?,?,?,?)",
                ("identity", "s-old", "node:FUNCTION:unique", "FUNCTION",
                 "unique", "unique", "c", "native.c"))
    old.execute("PRAGMA user_version=1")
    old.commit()
    old.close()
    for _ in range(2):
        db = Database(path)
        try:
            row = db.node_by_id("identity", "s-old", "node:FUNCTION:unique")
            assert row["identity_schema_version"] == "symbol-identity/v1"
            assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            db.close()
