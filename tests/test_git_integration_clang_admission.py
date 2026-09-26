"""The Git candidate audit must obtain Clang support through the real issuer."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from rintel.db import Database
from rintel.design_lifecycle.models import DesignRef
from rintel.git_integration_authority import GitIntegrationAuthority
from rintel.indexer import Indexer

pytestmark = pytest.mark.skipif(
    not Path("/usr/bin/clang").is_file(),
    reason="bounded AppleClang direct-call pilot requires /usr/bin/clang")


@dataclass(frozen=True)
class _Change:
    id: str
    repo_id: str
    scope: dict
    implementation: dict
    design_revision: object


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("build_context", ["valid", "missing", "wrong_tu",
                                            "multi_source"])
def test_audit_issues_only_exact_candidate_clang_support(
        tmp_path: Path, monkeypatch, build_context: str):
    repo = tmp_path / "integration"
    repo.mkdir()
    (repo / "sample.c").write_text(
        "int sink(int x) { return x * 2; }\n"
        "int source(int x) { return sink(x + 1); }\n"
        + ("int source2(int x) { return sink(x + 2); }\n"
           if build_context == "multi_source" else ""), encoding="utf-8")
    if build_context != "missing":
        compdb = [{"directory": ".",
                   "file": "other.c" if build_context == "wrong_tu"
                           else "sample.c",
                   "arguments": ["/usr/bin/clang", "-std=c11", "-c",
                                 "sample.c", "-o", "sample.o"]}]
        (repo / "compile_commands.json").write_text(json.dumps(compdb),
                                                       encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    production = Database(tmp_path / "production.sqlite")
    try:
        base = Indexer(production, repo, repo_id="pilot-c", commit=head).index()
        before = production.current_snapshot("pilot-c")
        nodes = {node["name"]: node for node in production.all_nodes(
            "pilot-c", base.snapshot_id) if node["kind"] == "FUNCTION"
                 and node["language"] == "c"}
        flow = {
            "flow": {"id": "expected-flow", "repo_id": "pilot-c",
                     "snapshot_id": base.snapshot_id},
            "blocks": [{"id": "src", "name": "source", "kind": "function",
                        "state": "existing"},
                       {"id": "dst", "name": "sink", "kind": "function",
                        "state": "existing"}],
            "bindings": [
                {"block_id": "src", "canonical_symbol_id": nodes["source"]["id"]},
                {"block_id": "dst", "canonical_symbol_id": nodes["sink"]["id"]}],
            "ports": [],
            "nets": [{"id": "expected-call", "kind": "control",
                      "source_block_id": "src", "target_block_id": "dst"}],
        }
        if build_context == "multi_source":
            flow["blocks"].append({"id": "src2", "name": "source2",
                                   "kind": "function", "state": "existing"})
            flow["bindings"].append({"block_id": "src2",
                                     "canonical_symbol_id": nodes["source2"]["id"]})
            flow["nets"].append({"id": "second-call", "kind": "control",
                                 "source_block_id": "src2",
                                 "target_block_id": "dst"})
        change = _Change(
            id="change-pilot", repo_id="pilot-c", scope={}, implementation={},
            design_revision=SimpleNamespace(
                id="design-pilot", expected_changes=(),
                flow_model_ref=DesignRef(identity="expected-flow",
                                         revision="flow-rev")))
        import rintel.git_integration_authority as module
        from rintel.flow.service import FlowService
        monkeypatch.setattr(module, "DesignLifecycleService",
                            lambda _store: SimpleNamespace(
                                get_change=lambda _id: change))
        monkeypatch.setattr(FlowService, "get_flow", lambda _self, _id: flow)
        monkeypatch.setattr(module, "run_design_drc",
                            lambda _expected, _scope: {"acceptable": True})
        monkeypatch.setattr(module, "project_change_alignments",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            GitIntegrationAuthority, "_execute",
            lambda _self, _integration, _change, _profile, kind, _params:
                {"result": "PASS", "execution_id": f"{kind}-test"})
        integration = {
            "change_id": change.id, "integration_id": "integration-pilot",
            "repository_identity": "pilot-repo", "repository_root": str(repo),
            "integration_worktree": str(repo), "integration_head": head,
            "integration_tree": tree, "target_branch": "main",
            "target_head": head, "merge_bases": [head],
            "candidate_commits": [head], "workspace_ids": ["pilot-workspace"],
            "git_status": "CLEAN_MERGE", "conflicted_files": [],
            "agent_scope_status": "IN_SCOPE",
        }
        projection = GitIntegrationAuthority(
            tmp_path / "authority", production_store=production,
            execution_authority=None).audit(
                integration, build_profile_id="build", test_profile_id="test")
        candidate_revision = projection["candidate_index"][
            "candidate_canonical_revision"]
        candidate_path = GitIntegrationAuthority(
            tmp_path / "authority", production_store=production,
            execution_authority=None)._candidate_path(integration)
        candidate = Database(candidate_path)
        try:
            assert candidate.snapshot(candidate_revision)["commit_sha"] == head
            assert candidate.current_snapshot("pilot-c") == candidate_revision
            assert production.current_snapshot("pilot-c") == before
            edge_id = (f"edge:CALLS:{nodes['source']['id']}:"
                       f"{nodes['sink']['id']}")
            support = candidate.support_receipts("pilot-c", candidate_revision,
                                                 edge_id)
            if build_context == "valid":
                assert any(row["lane_id"] == "clang_provider" for row in support), (
                    projection["candidate_index"]["clang_support_attempts"])
                assert projection["design"]["lvs"]["result"][
                    "overall_status"] == "MATCH"
                assert projection["candidate_index"]["clang_support_attempts"][
                    0]["status"] == "ISSUED"
            elif build_context == "multi_source":
                attempts = projection["candidate_index"]["clang_support_attempts"]
                assert sorted(item["status"] for item in attempts) == [
                    "ISSUED", "SUPERSEDED"]
                assert projection["design"]["lvs"]["result"][
                    "overall_status"] == "UNKNOWN"
                assert projection["merge_eligibility"] == "NOT_MERGE_ELIGIBLE"
            else:
                assert not any(row["lane_id"] == "clang_provider"
                               for row in support)
                assert projection["design"]["lvs"]["result"][
                    "overall_status"] == "UNKNOWN"
                assert projection["merge_eligibility"] == "NOT_MERGE_ELIGIBLE"
        finally:
            candidate.close()
    finally:
        production.close()
