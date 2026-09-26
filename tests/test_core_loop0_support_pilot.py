"""A real C direct-call probe of existing production support authority."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rintel.coverage_authority.issuer import CoverageAuthority
from rintel.coverage_authority.validation import applicable_complete
from rintel.clang_provider.compile_context import load_translation_unit
from rintel.clang_provider.extractor import extract_facts
from rintel.clang_provider.frontend import ClangFrontend, detect_clang_identity
from rintel.db import Database
from rintel.design_lifecycle.repository import LifecycleRepository
from rintel.design_lifecycle.models import DesignRef
from rintel.flow.projection import FlowProjectionService
from rintel.git_integration_authority import GitIntegrationAuthority
from rintel.indexer import Indexer
from rintel.lvs.flow_runner import _edges_for


def test_clang_direct_call_support_does_not_overstate_lvs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.c").write_text(
        "int sink(int x) { return x * 2; }\n"
        "int source(int x) { return sink(x + 1); }\n", encoding="utf-8")
    (repo / "homonym.py").write_text(
        "def sink(x):\n    return x\n", encoding="utf-8")
    compdb = repo / "compile_commands.json"
    compdb.write_text(json.dumps([{
        "directory": str(repo), "file": str(repo / "sample.c"),
        "arguments": ["/usr/bin/clang", "-std=c11", "-c", "sample.c", "-o", "sample.o"],
    }]))
    db = Database(tmp_path / "store.sqlite")
    try:
        indexed = Indexer(db, repo, repo_id="pilot-c").index()
        functions = {n["name"]: n for n in db.all_nodes("pilot-c", indexed.snapshot_id)
                     if n["kind"] == "FUNCTION" and n["language"] == "c"}
        assert {"source", "sink"} <= functions.keys()
        row = json.loads(compdb.read_text())[0]
        unit = load_translation_unit(row, detect_clang_identity("/usr/bin/clang"))
        frontend = ClangFrontend().analyze(unit)
        facts = extract_facts(frontend.ast, unit, repo_root=repo)
        direct = [f for f in facts if f["kind"] == "CALL"
                  and f["subject"]["name"] == "source"
                  and f["object"]["entity"]["name"] == "sink"]
        assert len(direct) == 1
        assert direct[0]["claim"]["execution_modality"] == "MUST"
        assert direct[0]["source_span"]["file"] == "sample.c"
        assert direct[0]["source_span"]["start_line"] == 2
        edge_id = f"edge:CALLS:{functions['source']['id']}:{functions['sink']['id']}"
        receipts = db.support_receipts("pilot-c", indexed.snapshot_id, edge_id)
        projected = [e for e in _edges_for(db, "pilot-c", indexed.snapshot_id)
                     if e["source"] == functions["source"]["id"]
                     and e["target"] == functions["sink"]["id"]]
        assert receipts
        assert all(r["truth_class"] == "INFERRED" for r in receipts)
        assert all(r["execution_modality"] == "MAY" for r in receipts)
        assert all(r["coverage"] == "UNKNOWN" for r in receipts)
        assert projected and projected[0]["target_resolution"] == "UNKNOWN"

        cert = CoverageAuthority(db).issue_direct_static_call(
            repo_id="pilot-c", base_revision=indexed.snapshot_id,
            compile_commands=compdb, translation_unit="sample.c",
            subject_id=functions["source"]["id"]).to_dict()
        assert cert["completeness"] == "COMPLETE"
        assert cert["subject"] == functions["source"]["id"]
        assert cert["direct_call_targets"] == {functions["sink"]["id"]: 1}
        assert db.current_snapshot("pilot-c") == cert["canonical_revision"]
        stored = LifecycleRepository(db).coverage_certificates(
            "pilot-c", cert["canonical_revision"], functions["source"]["id"],
            "DIRECT_STATIC_CALL")
        assert len(stored) == 1 and stored[0]["id"] == cert["id"]
        issued_support = db.support_receipts(
            "pilot-c", cert["canonical_revision"], edge_id)
        clang_support = [r for r in issued_support
                         if r["lane_id"] == "clang_provider"]
        assert len(clang_support) == 1
        assert clang_support[0]["support_receipt_id"] in cert["support_receipt_ids"]
        assert (clang_support[0]["truth_class"],
                clang_support[0]["execution_modality"],
                clang_support[0]["target_resolution"],
                clang_support[0]["coverage"]) == (
                    "OBSERVED", "MAY", "EXACT", "UNKNOWN")
        assert any(r["lane_id"] == "legacy_builtin" for r in issued_support)
        issued_projected = [e for e in _edges_for(
            db, "pilot-c", cert["canonical_revision"])
            if e["source"] == functions["source"]["id"]
            and e["target"] == functions["sink"]["id"]]
        assert len(issued_projected) == 1
        assert issued_projected[0]["target_resolution"] == "UNKNOWN"
        assert issued_projected[0]["coverage"] == "PARTIAL"
        # The existing join is for proved absence within this exact body, not
        # a promotion of the observed positive source -> sink edge.
        absence, reason = applicable_complete(
            cert, repo_id="pilot-c", revision=cert["canonical_revision"],
            expected={"kind": "CALLS", "call_semantics": "DIRECT_STATIC_CALL",
                      "source": functions["source"]["id"],
                      "target": functions["source"]["id"]}, store=db)
        assert absence and reason == "bounded_direct_static_domain_complete"
        projection = FlowProjectionService._for_design_lifecycle(db).from_symbol(
            "pilot-c", indexed.snapshot_id, functions["source"]["id"],
            name="C direct-call evidence pilot", include_callees=True)
        flow_id = projection["flow"]["id"]
        change = SimpleNamespace(repo_id="pilot-c", design_revision=SimpleNamespace(
            flow_model_ref=DesignRef(identity=flow_id, revision="pilot-flow-ref")))
        git_lvs = GitIntegrationAuthority(
            tmp_path / "git-integration", production_store=db,
            execution_authority=None)._lvs(
                change, db, cert["canonical_revision"], str(repo))
        # CORE-LOOP0's historical UNKNOWN was correct under its then-current
        # deterministic-call LVS adapter. The later versioned static-presence
        # rule may match this Flow control net without upgrading the source
        # claim or treating the absence certificate as positive support.
        assert git_lvs["overall_status"] == "MATCH"
        assert len(git_lvs["call_diffs"]) == 1
        diff = git_lvs["call_diffs"][0]
        assert diff["status"] == "MATCH"
        assert diff["coverage"] == "UNKNOWN"
        assert diff["code_witness"]["static_presence"] == "PRESENT"
        assert diff["code_witness"]["target_resolution"] == "EXACT"
        assert diff["code_witness"]["execution_modality"] == "MAY"
        assert diff["code_witness"]["runtime_state"] == "UNMEASURED"
        assert diff["code_witness"]["runtime_evidence_used"] is False
        assert diff["code_witness"]["support_receipt_ids"] == [
            clang_support[0]["support_receipt_id"]]
    finally:
        db.close()
