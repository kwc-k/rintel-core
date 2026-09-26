"""Static direct-call presence is not execution or runtime observation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

from rintel.clang_provider.compile_context import load_translation_unit
from rintel.clang_provider.extractor import extract_facts
from rintel.clang_provider.frontend import ClangFrontend, detect_clang_identity
from rintel.coverage_authority import issuer as coverage_issuer
from rintel.db import Database
from rintel.design_lifecycle.models import DesignRef
from rintel.evidence_authority.positive_relation import direct_static_call_presence
from rintel.flow.service import FlowService
from rintel.git_integration_authority import GitIntegrationAuthority
from rintel.indexer import Indexer
from rintel.lvs.engine import run_lvs
from rintel.lvs.flow_runner import _edges_for, lvs_flow
from rintel.synthesis.flow_adapter import code_side_from_store


def _repo(tmp_path: Path, source: str) -> tuple[Path, Database, str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.c").write_text(source, encoding="utf-8")
    compdb = repo / "compile_commands.json"
    compdb.write_text(json.dumps([{
        "directory": str(repo), "file": str(repo / "sample.c"),
        "arguments": ["/usr/bin/clang", "-std=c11", "-c", "sample.c",
                      "-o", "sample.o"],
    }]), encoding="utf-8")
    db = Database(tmp_path / "store.sqlite")
    sid = Indexer(db, repo, repo_id="pilot-c").index().snapshot_id
    nodes = {n["name"]: n for n in db.all_nodes("pilot-c", sid)
             if n["kind"] == "FUNCTION"}
    return repo, db, sid, nodes


def test_conditional_direct_call_has_static_presence_not_runtime_proof(
        tmp_path: Path, monkeypatch):
    repo, db, sid, nodes = _repo(tmp_path,
        "int sink(int x) { return x * 2; }\n"
        "int source(int x) { if (x > 0) return sink(x); return x; }\n")
    try:
        unit = load_translation_unit(
            json.loads((repo / "compile_commands.json").read_text())[0],
            detect_clang_identity("/usr/bin/clang"))
        facts = extract_facts(ClangFrontend().analyze(unit).ast, unit,
                              repo_root=repo)
        direct = [f for f in facts if f["kind"] == "CALL"
                  and f["subject"]["name"] == "source"
                  and f["object"]["entity"]["name"] == "sink"]
        assert len(direct) == 1
        assert direct[0]["source_span"]["start_line"] == 2
        # The provider's raw MUST is not an every-path guarantee: this call
        # sits under a condition. The production issuer preserves MAY.
        host_type = coverage_issuer.ProviderHost
        monkeypatch.setattr(coverage_issuer, "ProviderHost",
            lambda _argv, **kwargs: host_type([
                sys.executable, "-c",
                "from rintel.clang_provider.process import run; "
                "raise SystemExit(run())"], **kwargs))
        cert = coverage_issuer.CoverageAuthority(db).issue_direct_static_call(
            repo_id="pilot-c", base_revision=sid,
            compile_commands=repo / "compile_commands.json",
            translation_unit="sample.c", subject_id=nodes["source"]["id"]
        ).to_dict()
        revision = cert["canonical_revision"]
        edge_id = (f"edge:CALLS:{nodes['source']['id']}:"
                   f"{nodes['sink']['id']}")
        edge = next(e for e in db.all_edges("pilot-c", revision)
                    if e["id"] == edge_id)
        supports = db.support_receipts("pilot-c", revision, edge_id)
        clang = next(r for r in supports if r["lane_id"] == "clang_provider")
        assert cert["completeness"] == "COMPLETE"
        assert (clang["truth_class"], clang["execution_modality"],
                clang["target_resolution"], clang["coverage"]) == (
                    "OBSERVED", "MAY", "EXACT", "UNKNOWN")
        decision = direct_static_call_presence(
            db, "pilot-c", revision, edge, supports, source_root=repo)
        assert decision["state"] == "PRESENT"
        assert decision["runtime_state"] == "UNMEASURED"
        assert decision["evidence_authority"] == "CANONICAL_CANDIDATE"
        assert decision["provider_status"] == "PILOT_ONLY"
        assert clang["support_receipt_id"] in decision["support_receipt_ids"]
        projection = next(e for e in _edges_for(
            db, "pilot-c", revision, source_root=repo)
            if e["source"] == nodes["source"]["id"]
            and e["target"] == nodes["sink"]["id"])
        assert projection["static_presence"] == "PRESENT"
        assert projection["execution_modality"] == "MAY"
        assert projection["coverage"] != "COMPLETE"
        assert projection["static_presence_support_dimensions"] == [{
            "support_receipt_id": clang["support_receipt_id"],
            "truth_class": "OBSERVED", "target_resolution": "EXACT",
            "coverage": "UNKNOWN", "execution_modality": "MAY"}]
        synthesis = code_side_from_store(db, "pilot-c", revision, str(repo))
        synthesis_call = next(e for e in synthesis.edges
                              if e["source"] == nodes["source"]["id"]
                              and e["target"] == nodes["sink"]["id"])
        assert synthesis_call["static_presence"] == "PRESENT"
        assert synthesis_call["execution_modality"] == "MAY"
        assert synthesis_call["coverage"] != "COMPLETE"
        assert synthesis.call_capability == "PARTIAL"
        flow = {
            "flow": {"id": "design-flow", "repo_id": "pilot-c",
                     "snapshot_id": sid},
            "blocks": [
                {"id": "src", "name": "source", "kind": "function",
                 "state": "existing"},
                {"id": "dst", "name": "sink", "kind": "function",
                 "state": "existing"}],
            "bindings": [
                {"block_id": "src", "canonical_symbol_id": nodes["source"]["id"]},
                {"block_id": "dst", "canonical_symbol_id": nodes["sink"]["id"]}],
            "ports": [],
            "nets": [{"id": "expected", "kind": "control",
                      "source_block_id": "src", "target_block_id": "dst"}],
        }
        monkeypatch.setattr(FlowService, "get_flow", lambda _self, _id: flow)
        rest_lvs = lvs_flow(db, "design-flow")
        assert rest_lvs["call_diffs"][0]["status"] == "MATCH"
        assert rest_lvs["_meta"]["call_capability"] == "PARTIAL"
        change = SimpleNamespace(repo_id="pilot-c", design_revision=SimpleNamespace(
            flow_model_ref=DesignRef(identity="design-flow", revision="rev")))
        result = GitIntegrationAuthority(
            tmp_path / "git-state", production_store=db,
            execution_authority=None)._lvs(change, db, revision, str(repo))
        call_diff = result["call_diffs"][0]
        assert call_diff["status"] == "MATCH"
        assert call_diff["coverage"] == "UNKNOWN"
        assert call_diff["code_witness"]["execution_modality"] == "MAY"
        assert call_diff["code_witness"]["evidence_authority"] == (
            "CANONICAL_CANDIDATE")
        assert call_diff["code_witness"]["runtime_evidence_used"] is False
        for change in ({"lane_id": "ripwire_reference"},
                       {"admission_id": "self-reported"},
                       {"support_receipt_id": "support:self-reported"},
                       {"source_digest": "sha256:stale"},
                       {"source_ids": [nodes["sink"]["id"],
                                       nodes["source"]["id"]]}):
            forged = {**clang, **change}
            assert direct_static_call_presence(
                db, "pilot-c", revision, edge, [forged],
                source_root=repo)["state"] == "UNKNOWN"
        compdb = repo / "compile_commands.json"
        old_compdb = compdb.read_text(encoding="utf-8")
        compdb.write_text(old_compdb.replace("-std=c11", "-std=c17"),
                          encoding="utf-8")
        assert direct_static_call_presence(
            db, "pilot-c", revision, edge, supports,
            source_root=repo)["state"] == "UNKNOWN"
        compdb.write_text(old_compdb, encoding="utf-8")
        (repo / "sample.c").write_text(
            "int sink(int x) { return x * 3; }\n"
            "int source(int x) { if (x > 0) return sink(x); return x; }\n",
            encoding="utf-8")
        assert direct_static_call_presence(
            db, "pilot-c", revision, edge, supports,
            source_root=repo)["state"] == "UNKNOWN"
    finally:
        db.close()


def test_missing_flow_call_without_bounded_certificate_stays_unknown(
        tmp_path: Path, monkeypatch):
    repo, db, sid, nodes = _repo(tmp_path,
        "int sink(int x) { return x * 2; }\n"
        "int source(int x) { return x + 1; }\n")
    try:
        assert db.unresolved_count("pilot-c", sid) == 0
        assert code_side_from_store(db, "pilot-c", sid, str(repo)
                                    ).call_capability == "PARTIAL"
        flow = {
            "flow": {"id": "design-flow", "repo_id": "pilot-c",
                     "snapshot_id": sid},
            "blocks": [
                {"id": "src", "name": "source", "kind": "function",
                 "state": "existing"},
                {"id": "dst", "name": "sink", "kind": "function",
                 "state": "existing"}],
            "bindings": [
                {"block_id": "src", "canonical_symbol_id": nodes["source"]["id"]},
                {"block_id": "dst", "canonical_symbol_id": nodes["sink"]["id"]}],
            "ports": [],
            "nets": [{"id": "expected", "kind": "control",
                      "source_block_id": "src", "target_block_id": "dst"}],
        }
        monkeypatch.setattr(FlowService, "get_flow", lambda _self, _id: flow)
        rest_lvs = lvs_flow(db, "design-flow")
        assert rest_lvs["call_diffs"][0]["status"] == "UNKNOWN"
        assert rest_lvs["_meta"]["call_capability"] == "PARTIAL"
        change = SimpleNamespace(repo_id="pilot-c", design_revision=SimpleNamespace(
            flow_model_ref=DesignRef(identity="design-flow", revision="rev")))
        result = GitIntegrationAuthority(
            tmp_path / "git-state", production_store=db,
            execution_authority=None)._lvs(change, db, sid, str(repo))
        assert result["call_diffs"][0]["status"] == "UNKNOWN"
        assert result["acceptable"] is False
    finally:
        db.close()


def test_unscoped_may_or_candidate_set_is_not_a_deterministic_match():
    design = {
        "blocks": [
            {"id": "a", "binding": "node:a"},
            {"id": "b", "binding": "node:b"}],
        "nets": [{"id": "call", "kind": "control",
                  "source_block_id": "a", "target_block_id": "b"}],
    }
    for modality, resolution in (("MAY", "EXACT"),
                                 ("MUST", "CANDIDATE_SET")):
        result = run_lvs(design, {
            "functions": {"node:a": {}, "node:b": {}},
            "edges": [{"kind": "CALL", "source": "node:a",
                       "target": "node:b", "truth_class": "OBSERVED",
                       "coverage": "COMPLETE",
                       "execution_modality": modality,
                       "target_resolution": resolution}],
        }).to_dict()
        assert result["call_diffs"][0]["status"] == "UNKNOWN"
