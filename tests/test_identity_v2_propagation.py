"""Identity-v2 propagation through public graph queries and RPP admission."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import json
import sys

import pytest

from rintel.db import Database
from rintel.clang_provider.compile_context import load_translation_unit
from rintel.clang_provider.frontend import detect_clang_identity
from rintel.clang_provider.model import digest
from rintel.analysis.contract import Coverage, ExecutionModality, TargetResolution, TruthClass
from rintel.evidence_authority import CanonicalStateRef, DEFAULT_ENGINE
from rintel.indexer import Indexer
from rintel.identity import canonical_provider_entity_id
from rintel.flow.projection import FlowProjectionService
from rintel.lvs.flow_runner import _edges_for
from rintel.lvs.signatures import build_code_side_from_rows
from rintel.mcp import published
from rintel.mcp import server as mcp_server
from rintel.model import Node
from rintel.provider_arch.canonical import IdentityAdmissionError, strict_identity
from rintel.provider_arch.contract import EvidenceCandidate, ProviderResult
from rintel.provider_arch.publication import CanonicalPublicationStore
from rintel.provider_protocol.host import _candidate, _canonical_entity
from rintel.provider_protocol.host import HostRunStatus, ProviderHost
from rintel.provider_protocol.model import AnalysisContract, BuildContext
from rintel.server.api.graph import neighborhood
from rintel.server.api.search import search as rest_search
from rintel.server.errors import ApiError
from rintel.store import FlowError


@pytest.fixture
def indexed(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "native.c").write_text(
        "int target(void) { return 1; }\n"
        "int unique(void) { return target(); }\n", encoding="utf-8")
    (root / "module.py").write_text(
        "def target():\n    return 2\n", encoding="utf-8")
    (root / "numeric.f90").write_text(
        "integer function target()\ntarget = 3\nend function target\n",
        encoding="utf-8")
    db = Database(tmp_path / "identity.sqlite")
    try:
        sid = Indexer(db, root, repo_id="identity").index().snapshot_id
        yield db, sid
    finally:
        db.close()


def test_unique_legacy_alias_traverses_v2_topology_and_path(indexed) -> None:
    db, sid = indexed
    nodes = db.all_nodes("identity", sid)
    caller = next(n for n in nodes if n["name"] == "unique")
    target = next(n for n in nodes if n["name"] == "target"
                  and n["language"] == "c")
    result = published.topology(db, {
        "repo_id": "identity", "root": "node:FUNCTION:unique",
        "direction": "out", "relations": ["CALL"]})
    assert result["root"] == caller["id"]
    assert {n["canonical_id"] for n in result["nodes"]} >= {
        caller["id"], target["id"]}
    assert result["edges"][0]["source"] == caller["id"]
    assert result["edges"][0]["target"] == target["id"]
    assert all(n["identity_schema_version"] == "symbol-identity/v2"
               for n in result["nodes"] if n["canonical_id"].startswith("node:FUNCTION:"))

    path = published.find_path(db, {
        "repo_id": "identity", "source": "node:FUNCTION:unique",
        "target": target["id"], "relations": ["CALL"]})
    assert path["verdict"] == "FOUND"
    assert path["paths"][0]["hops"][0]["from"] == caller["id"]


def test_ambiguous_legacy_alias_fails_closed_in_topology_and_path(indexed) -> None:
    db, sid = indexed
    matches = published.search_symbols(db, {
        "repo_id": "identity", "query": "target"})
    assert len({r["canonical_id"] for r in matches if r["kind"] == "FUNCTION"}) == 3
    for result in (
        published.topology(db, {"repo_id": "identity", "root": "node:FUNCTION:target"}),
        published.find_path(db, {"repo_id": "identity",
                                 "source": "node:FUNCTION:unique",
                                 "target": "node:FUNCTION:target"}),
    ):
        assert result["error"]["code"] == "AMBIGUOUS_LEGACY_ID"
        assert len(result["error"]["candidates"]) == 2


def test_fresh_published_views_have_no_formal_v1_symbol_ids(indexed) -> None:
    db, sid = indexed
    rows = db.all_nodes("identity", sid)
    edges = db.all_edges("identity", sid)
    search_rows = published.search_symbols(db, {
        "repo_id": "identity", "query": "target"})
    rest_rows = rest_search("target", "identity", snapshot=sid,
                            limit=50, store=db)["items"]
    formal_ids = [node["id"] for node in rows
                  if node["kind"] not in {"REPOSITORY", "DIRECTORY", "FILE", "COMMIT"}]
    formal_ids += [endpoint for edge in edges
                   for endpoint in (edge["src_id"], edge["dst_id"])
                   if endpoint.startswith("node:FUNCTION:")]
    formal_ids += [row["canonical_id"] for row in search_rows
                   if row["kind"] == "FUNCTION"]
    formal_ids += [row["id"] for row in rest_rows
                   if row["kind"] == "FUNCTION"]
    assert formal_ids
    assert all(":v2:" in node_id for node_id in formal_ids)
    assert all(row["identity_schema_version"] == "symbol-identity/v2"
               for row in search_rows if row["kind"] == "FUNCTION")


def test_rest_graph_legacy_alias_resolves_or_rejects_ambiguity(indexed) -> None:
    db, sid = indexed
    result = neighborhood("identity", "node:FUNCTION:unique", snapshot=sid,
                          depth=1, node_budget=20, edge_budget=20,
                          direction="out", store=db)
    assert len(result["nodes"]) >= 2
    assert all(n["identity_schema_version"] == "symbol-identity/v2"
               for n in result["nodes"] if n["kind"] == "FUNCTION")
    with pytest.raises(ApiError) as caught:
        neighborhood("identity", "node:FUNCTION:target", snapshot=sid,
                     depth=1, node_budget=20, edge_budget=20,
                     direction="out", store=db)
    assert caught.value.code == "ambiguous_legacy_id"


def test_actual_mcp_tool_boundary_keeps_distinct_v2_homonyms(
        indexed, monkeypatch) -> None:
    db, sid = indexed
    monkeypatch.setattr(mcp_server, "_STORE", db)
    result = mcp_server.mcp_call("search_symbols", {
        "repo_id": "identity", "query": "target", "limit": 10})
    cands = [row for row in result["matches"]
             if row["kind"] == "FUNCTION"]
    assert len({row["canonical_id"] for row in cands}) == 3
    assert all(row["identity_schema_version"] == "symbol-identity/v2"
               for row in cands)
    topology = mcp_server.mcp_call("query_topology", {
        "repo_id": "identity", "root": "node:FUNCTION:unique",
        "relations": ["CALL"], "direction": "out"})
    assert topology["root"].startswith("node:FUNCTION:v2:")
    assert all(edge["source"].startswith("node:FUNCTION:v2:")
               for edge in topology["edges"])
    ambiguous = mcp_server.mcp_call("find_path", {
        "repo_id": "identity", "source": "node:FUNCTION:unique",
        "target": "node:FUNCTION:target"})
    assert ambiguous["error"]["code"] == "AMBIGUOUS_LEGACY_ID"


def test_mcp_marks_frozen_name_keyed_lane_as_unbound_reference(
        indexed, monkeypatch) -> None:
    db, _ = indexed
    monkeypatch.setattr(mcp_server, "_STORE", db)
    monkeypatch.setattr(mcp_server, "_available_lanes", lambda: ("old",))
    monkeypatch.setattr(mcp_server, "bundle", lambda lane: {
        "nodes": [{"canonical_symbol_id": "node:FUNCTION:target",
                   "file": "old.c", "language": "c"}], "edges": []})
    result = mcp_server.mcp_call("search_symbols", {
        "query": "target", "limit": 1})
    assert result["matches"][0]["canonical_id"] == "node:FUNCTION:target"
    assert result["matches"][0]["identity_authority"] == "REFERENCE_UNBOUND"
    assert result["matches"][0]["canonical_id_is_current"] is False
    assert result["identity_boundary"]


def test_mcp_flow_reference_result_never_becomes_current_identity(monkeypatch) -> None:
    _, spec = mcp_server.TOOLS["query_flow"]
    monkeypatch.setitem(mcp_server.TOOLS, "query_flow", (
        lambda _args: {"flows": [{"id": "historical-flow",
                                   "canonical_symbol_id": "node:FUNCTION:foo"}]},
        spec))
    result = mcp_server.mcp_call("query_flow", {})
    assert result["identity_boundary"] == "REFERENCE_UNBOUND"
    assert result["flows"][0]["canonical_symbol_id"] == "node:FUNCTION:foo"


def test_flow_projection_binds_v2_ids_and_rejects_ambiguous_legacy(indexed) -> None:
    db, sid = indexed
    service = FlowProjectionService._for_design_lifecycle(db)
    projected = service.from_symbol(
        "identity", sid, "node:FUNCTION:unique", include_callees=True)
    bound = {row["canonical_symbol_id"] for row in projected["bindings"]}
    assert len(bound) == 2
    assert all(":v2:" in value for value in bound)
    code = build_code_side_from_rows(
        db.all_nodes("identity", sid), _edges_for(db, "identity", sid),
        db.repo("identity")["root_path"], sid)
    assert bound <= set(code.functions)
    assert all(":v2:" in edge["source"] and ":v2:" in edge["target"]
               for edge in code.edges if edge["kind"] == "CALL")
    with pytest.raises(FlowError) as caught:
        service.from_symbol("identity", sid, "node:FUNCTION:target")
    assert caught.value.code == "ambiguous_legacy_id"


def test_two_file_static_functions_stay_distinct_through_search_and_path(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.c").write_text(
        "static int foo(void) { return 1; }\n"
        "int caller_a(void) { return foo(); }\n", encoding="utf-8")
    (root / "b.c").write_text(
        "static int foo(void) { return 2; }\n"
        "int caller_b(void) { return foo(); }\n", encoding="utf-8")
    db = Database(tmp_path / "static.sqlite")
    try:
        sid = Indexer(db, root, repo_id="static").index().snapshot_id
        rows = db.all_nodes("static", sid)
        foos = {node["path"]: node for node in rows if node["name"] == "foo"}
        assert len({row["id"] for row in foos.values()}) == 2
        searched = published.search_symbols(db, {
            "repo_id": "static", "query": "foo"})
        assert {row["canonical_id"] for row in searched
                if row["name"] == "foo"} == {
                    row["id"] for row in foos.values()}
        for path, caller_name in (("a.c", "caller_a"), ("b.c", "caller_b")):
            caller = next(node for node in rows if node["name"] == caller_name)
            graph = published.topology(db, {
                "repo_id": "static", "root": caller["id"],
                "direction": "out", "relations": ["CALL"]})
            assert {edge["target"] for edge in graph["edges"]} == {
                foos[path]["id"]}
            path_result = published.find_path(db, {
                "repo_id": "static", "source": caller["id"],
                "target": foos[path]["id"], "relations": ["CALL"]})
            assert path_result["verdict"] == "FOUND"
    finally:
        db.close()


def test_rpp_host_computes_v2_identity_from_local_descriptor() -> None:
    for node in (
        Node("FUNCTION", "foo", "foo", "c", "a.c"),
        Node("FUNCTION", "foo", "b.c::foo", "c", "b.c",
             meta={"static": True}),
        Node("FUNCTION", "foo", "module.foo", "python", "module.py"),
        Node("FUNCTION", "main", "main", "c", "app.c",
             meta={"definition_source_scoped": True}),
        Node("SUBROUTINE", "solver", "solver", "fortran", "solver.f90",
             meta={"definition_source_scoped": True}),
    ):
        entity = {"local_id": "provider-owned", "kind": node.kind,
                  "name": node.name, "qualified_name": node.qname,
                  "path": node.path, "language": node.language}
        assert _canonical_entity(entity) == node.canonical_id
    assert _canonical_entity({"kind": "SUBROUTINE", "name": "solver",
                              "qualified_name": "solver", "path": "solver.f90",
                              "language": "FORTRAN77"}) == Node(
                                  "SUBROUTINE", "solver", "solver",
                                  "fortran", "solver.f90",
                                  meta={"definition_source_scoped": True}).canonical_id


def test_rpp_cxx_overload_without_semantic_discriminator_fails_closed() -> None:
    entity = {"local_id": "clang:foo", "kind": "FUNCTION", "name": "foo",
              "qualified_name": "foo", "path": "overloads.cpp",
              "language": "c++"}
    with pytest.raises(ValueError, match="overload identity"):
        canonical_provider_entity_id(entity)


def test_generic_canonicalizer_rejects_formal_v1_symbol_identity() -> None:
    candidate = SimpleNamespace(provider_id="untrusted-provider",
                                provider_fact_id="provider-local:1")
    with pytest.raises(IdentityAdmissionError, match="v2"):
        strict_identity("node:FUNCTION:foo", candidate, "subject")


def test_provider_admission_recomputes_v2_and_rejects_forged_endpoint() -> None:
    caller = {"local_id": "clang:caller", "kind": "FUNCTION", "name": "caller",
              "qualified_name": "caller", "path": "native.c", "language": "c"}
    target = {"local_id": "clang:target", "kind": "FUNCTION", "name": "target",
              "qualified_name": "target", "path": "native.c", "language": "c"}
    raw = {"subject": caller, "object": {"entity": target}, "kind": "CALL"}
    candidate = EvidenceCandidate(
        provider_id="rintel-clang", provider_fact_id="clang:call:1",
        subject=_canonical_entity(caller), predicate="CALL",
        object=_canonical_entity(target), source_span=None,
        truth_class=TruthClass.OBSERVED, coverage=Coverage.PARTIAL,
        resolution=TargetResolution.EXACT, revision_input="snapshot-1",
        execution_modality=ExecutionModality.MUST,
        witness={"provider_local": raw})
    state = CanonicalStateRef("prior", "sha256:prior")
    result = ProviderResult("rintel-clang", "provider-clang0.1",
                            "snapshot-1", facts=(candidate,))
    batch = DEFAULT_ENGINE.prepare_publication(
        result, lane_id="clang_provider", state=state)
    assert len(batch.reconciliation.evidence) == 1
    evidence = batch.reconciliation.evidence[0]
    assert evidence.subject == _canonical_entity(caller)
    assert evidence.object == _canonical_entity(target)
    forged = replace(candidate, object=_canonical_entity({**target,
                                                           "qualified_name": "other"}))
    with pytest.raises(IdentityAdmissionError, match="does not match"):
        DEFAULT_ENGINE.prepare_publication(
            replace(result, facts=(forged,)), lane_id="clang_provider",
            state=state)


def test_external_clang_direct_call_publishes_only_v2_endpoints(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "native.c"
    source.write_text(
        "static int target(void) { return 1; }\n"
        "int caller(void) { return target(); }\n", encoding="utf-8")
    (root / "homonym.py").write_text(
        "def target():\n    return 2\n", encoding="utf-8")
    compdb = root / "compile_commands.json"
    row = {"directory": str(root), "file": str(source),
           "arguments": ["/usr/bin/clang", "-std=c11", "-c", "native.c",
                         "-o", "native.o"]}
    compdb.write_text(json.dumps([row]), encoding="utf-8")
    unit = load_translation_unit(row, detect_clang_identity("/usr/bin/clang"))
    contract = AnalysisContract(
        analysis_id="identity-v2-clang-direct", repo_id="identity",
        repo_snapshot="snapshot-for-identity-test", root=str(root),
        provider_config={"compile_commands": str(compdb),
                         "clang_executable": "/usr/bin/clang"},
        build_context_id=unit.semantic_command_digest,
        build_context=BuildContext(
            defines=unit.defines, include_paths=unit.include_paths,
            compiler_flags=unit.relevant_flags,
            language_standard=unit.language_standard, target=unit.target),
        input_content_digest=unit.source_digest, semantic_digest=None,
        semantic_identity_schema_version="clang-ast-v1",
        changed_scope=("native.c",), invalidated_scope=("native.c",),
        dependency_manifest={"native.c": ()},
        dependency_manifest_digest=digest({"native.c": ()}),
        projection_identity="canonical-evidence", projection_version="1")
    publication_store = CanonicalPublicationStore(tmp_path / "publication")
    result = ProviderHost(
        [sys.executable, "-c",
         "from rintel.clang_provider.process import run; raise SystemExit(run())"],
        staging_root=tmp_path / "staging", publication_store=publication_store,
        lane_id="clang_provider", timeout_s=60).run_analysis(
            contract, publication_revision="identity-v2-revision")
    assert result.status is HostRunStatus.PUBLISHED, result.error
    assert publication_store.current_revision() == "identity-v2-revision"
    evidence = json.loads((tmp_path / "publication" / "revisions" /
                           "identity-v2-revision" / "canonical_evidence.json")
                          .read_text(encoding="utf-8"))["evidence"]
    calls = [item for item in evidence if item["predicate"] == "CALL"]
    assert len(calls) == 1
    for item in evidence:
        for endpoint in (item["subject"], item["object"]):
            if isinstance(endpoint, str) and endpoint.startswith("node:") \
                    and not endpoint.startswith(("node:FILE:", "node:DIRECTORY:",
                                                 "node:REPOSITORY:", "node:COMMIT:")):
                assert ":v2:" in endpoint
    db = Database(tmp_path / "graph.sqlite")
    try:
        sid = Indexer(db, root, repo_id="identity").index().snapshot_id
        nodes = db.all_nodes("identity", sid)
        caller = next(n for n in nodes if n["name"] == "caller")
        c_target = next(n for n in nodes if n["name"] == "target"
                        and n["language"] == "c")
        python_target = next(n for n in nodes if n["name"] == "target"
                             and n["language"] == "python")
    finally:
        db.close()
    assert calls[0]["subject"] == caller["id"]
    assert calls[0]["object"] == c_target["id"]
    assert calls[0]["object"] != python_target["id"]


def test_fortran_alternate_definitions_do_not_choose_arbitrary_target(
        tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "target_a.f90").write_text(
        "subroutine target()\nend subroutine target\n", encoding="utf-8")
    (root / "target_b.f90").write_text(
        "subroutine target()\nend subroutine target\n", encoding="utf-8")
    (root / "caller.f90").write_text(
        "subroutine caller()\ncall target()\nend subroutine caller\n",
        encoding="utf-8")
    db = Database(tmp_path / "fortran.sqlite")
    try:
        result = Indexer(db, root, repo_id="fortran-alt").index()
        nodes = db.all_nodes("fortran-alt", result.snapshot_id)
        targets = [node for node in nodes if node["name"].lower() == "target"
                   and node["kind"] == "SUBROUTINE"]
        assert len(targets) == 2
        assert len({node["id"] for node in targets}) == 2
        caller = next(node for node in nodes if node["name"].lower() == "caller"
                      and node["kind"] == "SUBROUTINE")
        calls = [edge for edge in db.edges_for_node(
            "fortran-alt", result.snapshot_id, caller["id"])
            if edge["kind"] == "CALLS" and edge["src_id"] == caller["id"]]
        assert not any(edge["dst_id"] in {target["id"] for target in targets}
                       for edge in calls)
    finally:
        db.close()


def test_rpp_fortran_call_without_target_binding_stays_unknown() -> None:
    subject = {"local_id": "fortran:caller", "kind": "SUBROUTINE",
               "name": "caller", "qualified_name": "caller",
               "path": "caller.f90", "language": "fortran"}
    target = {"local_id": "fortran:target", "kind": "SUBROUTINE",
              "name": "target", "qualified_name": "target",
              "path": "caller.f90", "language": "fortran"}
    raw = {"provider_fact_id": "fortran:call:1", "subject": subject,
           "predicate": "CALL", "object": {"entity": target},
           "source_span": {"file": "caller.f90", "start_line": 2},
           "claim": {"observation_basis": "DIRECT",
                     "execution_modality": "UNKNOWN"}}
    provider = SimpleNamespace(provider_id="legacy-fortran",
                               provider_version="1", provider_config_digest="sha256:x")
    contract = SimpleNamespace(analysis_id="a", repo_snapshot="snapshot",
                               build_context_id="unknown-target-set")
    fact = _candidate(raw, provider=provider, contract=contract,
                      coverage=Coverage.PARTIAL)
    assert fact.subject.startswith("node:SUBROUTINE:v2:")
    assert fact.object is None
    assert fact.resolution is TargetResolution.UNKNOWN
    assert fact.witness["identity_boundary"] == (
        "FORTRAN_TARGET_REQUIRES_BUILD_CONTEXT_BINDING")
