"""Identity negative controls for the published legacy graph seam."""
from __future__ import annotations

from pathlib import Path

from rintel.analysis.binding import BindingStatus, bind_provider_symbol
from rintel.db import Database
from rintel.indexer import Indexer
from rintel.mcp import published as published_store
from rintel.server.api.search import search as rest_search


def _index(tmp_path: Path, files: dict[str, str], repo_id: str = "identity"):
    root = tmp_path / repo_id
    root.mkdir()
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    db = Database(tmp_path / f"{repo_id}.sqlite")
    result = Indexer(db, root, repo_id=repo_id).index()
    return db, root, result.snapshot_id


def test_c_call_never_resolves_to_python_same_name(tmp_path: Path):
    db, _, sid = _index(tmp_path, {
        "caller.c": "int caller(void) { return fabs(); }\n",
        "module.py": "def fabs():\n    return 1\n",
    })
    try:
        nodes = db.all_nodes("identity", sid)
        caller = next(n for n in nodes if n["name"] == "caller")
        python_fabs = next(n for n in nodes if n["name"] == "fabs")
        calls = [e for e in db.edges_for_node("identity", sid, caller["id"])
                 if e["kind"] == "CALLS"]
        assert not any(e["dst_id"] == python_fabs["id"] for e in calls)
    finally:
        db.close()


def test_existing_c_direct_call_keeps_its_c_target_with_python_homonym(
        tmp_path: Path):
    db, _, sid = _index(tmp_path, {
        "caller.c": "int fabs(void);\nint caller(void) { return fabs(); }\n",
        "native.c": "int fabs(void) { return 2; }\n",
        "module.py": "def fabs():\n    return 1\n",
    })
    try:
        nodes = db.all_nodes("identity", sid)
        caller = next(n for n in nodes if n["name"] == "caller")
        c_fabs = next(n for n in nodes if n["name"] == "fabs"
                      and n["language"] == "c")
        python_fabs = next(n for n in nodes if n["name"] == "fabs"
                           and n["language"] == "python")
        calls = [e for e in db.edges_for_node("identity", sid, caller["id"])
                 if e["kind"] == "CALLS" and e["src_id"] == caller["id"]]
        assert [e["dst_id"] for e in calls] == [c_fabs["id"]]
        assert c_fabs["id"] != python_fabs["id"]
    finally:
        db.close()


def test_same_qname_across_languages_has_distinct_identity(tmp_path: Path):
    db, root, sid = _index(tmp_path, {
        "a.c": "int calculate(void) { return 1; }\n",
    })
    (root / "b.f90").write_text(
        "real function calculate()\ncalculate = 1.0\nend function calculate\n",
        encoding="utf-8")
    try:
        updated = Indexer(db, root, repo_id="identity").index()
    finally:
        db.close()
    reopened = Database(tmp_path / "identity.sqlite")
    try:
        assert reopened.current_snapshot("identity") == updated.snapshot_id
        rows = [n for n in reopened.all_nodes("identity", updated.snapshot_id)
                if n["kind"] == "FUNCTION" and n["name"] == "calculate"]
        assert {n["language"] for n in rows} == {"c", "fortran"}
        assert len({n["id"] for n in rows}) == 2
        assert {n["language"] for n in reopened.all_nodes("identity", sid)
                if n["name"] == "calculate"} == {"c"}
    finally:
        reopened.close()


def test_provider_binding_requires_language_and_stored_identity(tmp_path: Path):
    db, _, sid = _index(tmp_path, {
        "a.c": "int fabs(void) { return 1; }\n",
    })
    try:
        bound = bind_provider_symbol(db, "identity", sid, "test", "fabs",
                                     "c", "a.c", kind_hint="FUNCTION")
        wrong = bind_provider_symbol(db, "identity", sid, "test", "fabs",
                                     "python", "a.c", kind_hint="FUNCTION")
        assert bound.binding_status is BindingStatus.EXACT
        assert wrong.binding_status is BindingStatus.UNBOUND
        assert bound.canonical_symbol_id == next(
            n["id"] for n in db.nodes_by_path("identity", sid, "a.c")
            if n["kind"] == "FUNCTION")
    finally:
        db.close()


def test_two_c_static_foo_have_separate_file_identity(tmp_path: Path):
    db, _, sid = _index(tmp_path, {
        "a.c": "static int foo(void) { return 1; }\nint caller_a(void) { return foo(); }\n",
        "b.c": "static int foo(void) { return 2; }\nint caller_b(void) { return foo(); }\n",
    })
    try:
        nodes = db.all_nodes("identity", sid)
        foos = {n["path"]: n for n in nodes
                if n["kind"] == "FUNCTION" and n["name"] == "foo"}
        assert set(foos) == {"a.c", "b.c"}
        assert foos["a.c"]["id"] != foos["b.c"]["id"]
        for path, caller_name in (("a.c", "caller_a"), ("b.c", "caller_b")):
            caller = next(n for n in nodes if n["name"] == caller_name)
            calls = [e for e in db.edges_for_node("identity", sid, caller["id"])
                     if e["kind"] == "CALLS" and e["src_id"] == caller["id"]]
            assert [e["dst_id"] for e in calls] == [foos[path]["id"]]
    finally:
        db.close()


def test_published_search_is_current_and_repository_scoped(tmp_path: Path):
    first, root, old_sid = _index(tmp_path, {
        "src/source_copy.c": "int past_only(void) { return 1; }\n",
    }, "first")
    second_root = tmp_path / "second"
    (second_root / "src").mkdir(parents=True)
    (second_root / "src/source_copy.c").write_text(
        "int current_only(void) { return 2; }\n", encoding="utf-8")
    second_sid = Indexer(first, second_root, repo_id="second").index().snapshot_id
    try:
        (root / "src/source_copy.c").write_text(
            "int current_only(void) { return 3; }\n", encoding="utf-8")
        new_sid = Indexer(first, root, repo_id="first").index().snapshot_id
        assert old_sid != new_sid
        assert first.current_snapshot("first") == new_sid
        assert first.current_snapshot("second") == second_sid
        assert {n["name"] for n in first.all_nodes("first", old_sid)} >= {"past_only"}
        historical = rest_search("past_only", "first", snapshot=old_sid,
                                 limit=50, store=first)
        assert historical["items"][0]["currentness"] == "HISTORICAL"
        assert historical["items"][0]["snapshot_id"] == old_sid
        assert historical["items"][0]["repo_id"] == "first"
        current_rest = rest_search("current_only", "first", limit=50,
                                   store=first)
        assert current_rest["items"][0]["currentness"] == "CURRENT"
        current = published_store.search_symbols(first, {"query": "current_only",
                                                         "repo_id": "first"})
        assert len(current) == 1
        assert current[0]["repo_id"] == "first"
        assert current[0]["snapshot_id"] == new_sid
        assert current[0]["currentness"] == "CURRENT"
        assert current[0]["file"] == "src/source_copy.c"
        both = published_store.search_symbols(first, {"query": "current_only"})
        assert {r["repo_id"] for r in both} == {"first", "second"}
        assert {r["snapshot_id"] for r in both} == {new_sid, second_sid}
        assert all(r["currentness"] == "CURRENT" for r in both)
        ambiguous = published_store.get_symbol(
            first, current[0]["canonical_id"])
        assert ambiguous["error"]["code"] == "AMBIGUOUS_SYMBOL"
        assert not published_store.search_symbols(first, {"query": "past_only",
                                                     "repo_id": "first"})
    finally:
        first.close()


def test_explicit_c_fortran_bridge_is_relation_not_identity_merge(tmp_path: Path):
    db, _, sid = _index(tmp_path, {
        "bridge.c": "void c_entry(void) {}\n",
        "bridge.f90": "subroutine bridge() bind(C, NAME=\"c_entry\")\n"
                      "end subroutine bridge\n",
    })
    try:
        nodes = db.all_nodes("identity", sid)
        c = next(n for n in nodes if n["name"] == "c_entry"
                 and n["language"] == "c")
        f = next(n for n in nodes if n["name"] == "bridge"
                 and n["language"] == "fortran")
        assert c["id"] != f["id"]
        bridges = [e for e in db.edges_for_node("identity", sid, f["id"])
                   if e["kind"] == "BINDS_TO"]
        assert len(bridges) == 1, (db.all_pending_edges("identity", sid),
                                   db.unresolved_rows("identity", sid))
        assert (bridges[0]["src_id"], bridges[0]["dst_id"]) == (f["id"], c["id"])
    finally:
        db.close()
