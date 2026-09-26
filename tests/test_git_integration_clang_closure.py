"""Real owner/API/Git closure for a bounded C11 direct-call DesignChange."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient

from rintel.db import Database
from rintel.indexer import Indexer


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_real_clang_candidate_to_production_closure(tmp_path: Path, monkeypatch):
    repo = tmp_path / "pilot-repository"
    repo.mkdir()
    (repo / "sample.c").write_text(
        "int sink(int value) { return value * 2; }\n"
        "int source(int value) { return value + 1; }\n"
        "int runner(int value) { return source(value) + sink(value); }\n",
        encoding="utf-8")
    (repo / "compile_commands.json").write_text(json.dumps([{
        "directory": ".", "file": "sample.c",
        "arguments": ["/usr/bin/clang", "-std=c11", "-c", "sample.c",
                      "-o", "sample.o"]}]), encoding="utf-8")
    (repo / "test_sample.py").write_text(
        "import pathlib, subprocess, tempfile, unittest\n"
        "class ContractTest(unittest.TestCase):\n"
        "    def test_source_calls_sink(self):\n"
        "        with tempfile.TemporaryDirectory() as tmp:\n"
        "            driver = pathlib.Path(tmp) / 'driver.c'\n"
        "            driver.write_text('int source(int); int main(void) { return source(2) != 6; }')\n"
        "            binary = pathlib.Path(tmp) / 'pilot-test'\n"
        "            subprocess.run(['/usr/bin/clang', '-std=c11', 'sample.c', str(driver), '-o', str(binary)], check=True)\n"
        "            subprocess.run([str(binary)], check=True)\n",
        encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Pilot", "-c", "user.email=pilot@example.invalid",
         "commit", "-qm", "baseline")
    base_commit = _git(repo, "rev-parse", "HEAD")
    db_path = tmp_path / "rintel-data" / "evidence.sqlite"
    db_path.parent.mkdir()
    db = Database(db_path)
    indexed = Indexer(db, repo, repo_id="pilot-c", commit=base_commit).index()
    nodes = {node["name"]: node for node in db.all_nodes(
        "pilot-c", indexed.snapshot_id) if node["kind"] == "FUNCTION"
             and node["language"] == "c"}
    assert {"runner", "source", "sink"} <= nodes.keys()
    db.close()

    python = str(Path(sys.executable).resolve())
    profiles = [
        {"id": "pilot-build", "version": "1", "kind": "BUILD",
         "repo_id": "pilot-c", "cwd": str(repo), "allowed_root": str(repo),
         "argv": ["/usr/bin/clang", "-std=c11", "-fsyntax-only", "sample.c"],
         "input_files": ["sample.c"], "timeout_seconds": 30},
        {"id": "pilot-test", "version": "1", "kind": "TEST",
         "repo_id": "pilot-c", "cwd": str(repo), "allowed_root": str(repo),
         "argv": [python, "-B", "-m", "unittest", "-q", "test_sample.py"],
         "input_files": ["sample.c", "test_sample.py"],
         "timeout_seconds": 30, "test_suite_id": "c11-direct-call",
         "result_policy": "exit_zero"},
    ]
    monkeypatch.delenv("RINTEL_DATABASE_URL", raising=False)
    monkeypatch.delenv("RINTEL_E2E_PROFILE", raising=False)
    monkeypatch.setenv("RINTEL_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("RINTEL_EXECUTION_PROFILES_JSON", json.dumps(profiles))
    monkeypatch.setenv("RINTEL_GIT_WORKSPACE_STATE_PATH",
                       str(tmp_path / "rintel-data" / "git-workspace"))
    monkeypatch.setenv("RINTEL_EXECUTION_ARTIFACTS_PATH",
                       str(tmp_path / "rintel-data" / "executions"))
    from rintel.server.settings import get_settings
    from rintel.server.app import create_app
    get_settings.cache_clear()
    app = create_app()
    token = app.state.local_owner_auth._take_operator_bootstrap_token()
    assert token

    with TestClient(app, base_url="http://127.0.0.1",
                    client=("127.0.0.1", 51423)) as client:
        def request(method: str, path: str, body: dict | None = None, *,
                    intent: str | None = None, expected: int = 200,
                    agent_session: str | None = None) -> dict:
            headers = {"origin": "http://127.0.0.1"} if method != "GET" else {}
            if intent:
                headers["x-rintel-owner-intent"] = intent
            if agent_session:
                headers["x-rintel-agent-session"] = agent_session
            response = client.request(method, "/api/v1" + path, json=body,
                                      headers=headers)
            assert response.status_code == expected, (
                method, path, response.status_code, response.text)
            return response.json()

        def command(change: dict, name: str, **fields) -> dict:
            return request("POST", f"/design-changes/{change['id']}/commands", {
                "command": name, "actor": "pilot-owner",
                "change_version": change["version"],
                "expected_design_revision": change["design_revision"]["id"],
                **fields})

        request("POST", "/local-owner/bootstrap", {"token": token})
        change = request("POST", "/design-changes", {
            "repo_id": "pilot-c", "base_canonical_revision": indexed.snapshot_id,
            "intent": "Source should call sink", "scope": {"files": ["sample.c"]},
            "actor": "pilot-owner"}, expected=201)
        change = command(change, "design_mutation", plane="flow",
                         operation="from_symbol", payload={
                             "repo_id": "pilot-c", "snapshot_id": indexed.snapshot_id,
                             "symbol": nodes["runner"]["id"],
                             "include_callees": True,
                             "name": "C11 direct-call pilot"})
        flow = change["mutation_result"]
        flow_id = flow["flow"]["id"]
        bound = {row["canonical_symbol_id"]: row["block_id"]
                 for row in flow["bindings"]}
        ports = {(row["block_id"], row["name"]): row["id"]
                 for row in flow["ports"]}
        source_block = bound[nodes["source"]["id"]]
        sink_block = bound[nodes["sink"]["id"]]
        for net in flow["nets"]:
            change = command(change, "design_mutation", plane="flow",
                             operation="delete_net", payload={
                                 "flow_id": flow_id, "net_id": net["id"]})
        change = command(change, "design_mutation", plane="flow",
                         operation="add_net", payload={
                             "flow_id": flow_id,
                             "source_port_id": ports[(source_block, "control_out")],
                             "target_port_id": ports[(sink_block, "control_in")],
                             "kind": "control", "label": "source calls sink"})
        claim = {"id": "source-to-sink", "kind": "planned_relation",
                 "relation_kind": "CALLS", "source": nodes["source"]["id"],
                 "target": nodes["sink"]["id"],
                 "justification": "bounded C11 direct call",
                 "support": [nodes["source"]["id"], nodes["sink"]["id"]],
                 "touches": ["sample.c"],
                 "expected_evidence": {"kind": "CALLS",
                                       "source": nodes["source"]["id"],
                                       "target": nodes["sink"]["id"]}}
        change = command(change, "plan", expected_changes=[claim])
        request("POST", f"/design-changes/{change['id']}/approvals", {
            "decision": "APPROVE", "expected_version": change["version"],
            "expected_design_revision": change["design_revision"]["id"]},
            intent="approve", expected=201)
        change = command(change, "begin_implementation",
                         expected_touched_scope={"files": ["sample.c"]})
        cid = change["id"]
        work = request("POST", f"/design-changes/{cid}/git-collaboration/workspaces",
                       {"agent_id": "pilot-agent", "task_id": "c11-direct-call",
                        "allowed_scope": ["sample.c"],
                        "allowed_operations": ["EDIT", "BUILD", "TEST"]},
                       intent="allocate-agent-worktree", expected=201)
        worktree = Path(work["worktree_path"])
        (worktree / "sample.c").write_text(
            "int sink(int value) { return value * 2; }\n"
            "int source(int value) { return sink(value + 1); }\n"
            "int runner(int value) { return source(value) + sink(value); }\n",
            encoding="utf-8")
        _git(worktree, "add", "sample.c")
        _git(worktree, "-c", "user.name=Pilot", "-c",
             "user.email=pilot@example.invalid", "commit", "-qm",
             "implement direct call")
        wid = work["workspace_id"]
        agent = work["agent_session_token"]
        for profile, kind in (("pilot-build", "BUILD"),
                              ("pilot-test", "TEST")):
            receipt = request(
                "POST", f"/design-changes/{cid}/git-collaboration/"
                f"workspaces/{wid}/executions",
                {"profile_id": profile, "kind": kind},
                agent_session=agent, expected=201)
            assert receipt["result"] == "PASS"
        submission = request(
            "POST", f"/design-changes/{cid}/git-collaboration/"
            f"workspaces/{wid}/submit", agent_session=agent)
        assert submission["scope_status"] == "IN_SCOPE"
        integration = request(
            "POST", f"/design-changes/{cid}/git-collaboration/integrations",
            {"workspace_ids": [wid], "target_branch": "main"},
            intent="create-integration", expected=201)
        iid = integration["integration_id"]
        audit = request(
            "POST", f"/design-changes/{cid}/git-collaboration/"
            f"integrations/{iid}/audit",
            {"build_profile_id": "pilot-build", "test_profile_id": "pilot-test"},
            intent="audit-integration", expected=201)
        assert audit["candidate_index"]["snapshot_commit"] == audit[
            "integration_head"]
        assert audit["candidate_index"]["production_current_before"] == (
            indexed.snapshot_id)
        assert audit["candidate_index"]["clang_support_attempts"]
        assert audit["candidate_index"]["clang_support_attempts"][0][
            "status"] == "ISSUED", audit["candidate_index"]
        assert audit["design"]["lvs"]["result"]["overall_status"] == "MATCH", (
            audit["design"]["lvs"]["result"]["call_diffs"],
            audit["candidate_index"]["clang_support_attempts"])
        assert audit["merge_eligibility"] == "MERGE_ELIGIBLE", audit["diagnostics"]
        merged = request(
            "POST", f"/design-changes/{cid}/git-collaboration/"
            f"integrations/{iid}/merge", intent="merge-integration")
        observed = request(
            "POST", f"/design-changes/{cid}/git-collaboration/"
            f"integrations/{iid}/observe", intent="observe-merged-integration",
            expected=201)
        assert observed["status"] == "CANONICAL_CURRENT_OBSERVED"
        assert observed["merged_git_commit"] == merged["source_revision"]
        assert observed["production_current_after"] != indexed.snapshot_id
        job = request("POST", f"/design-changes/{cid}/reindex-jobs",
                      {"actor": "pilot-owner"}, expected=202)
        for _ in range(100):
            status = request("GET", f"/jobs/{job['job_id']}")
            if status["status"] in {"done", "failed"}:
                break
            sleep(0.05)
        assert status["status"] == "done", status
        adopted = request("POST",
                          f"/design-changes/{cid}/reindex-jobs/"
                          f"{job['job_id']}/adopt", {"actor": "pilot-owner"})
        evaluated = command(adopted, "evaluate_expected_actual")
        assert evaluated["state"] != "IMPLEMENTING", evaluated
        assert evaluated["state"] == "EVIDENCE_MATCHED"
        assert evaluated["expected_actual"]["outcome"] == "MATCHED"
        assert evaluated["lvs_result"]["overall_status"] == "MATCH"
        assert evaluated["implementation"]["current_evidence_revision"] == (
            observed["production_current_after"])
        assert _git(repo, "rev-parse", "HEAD") == merged["source_revision"]
        production = Database(db_path)
        try:
            current = production.current_snapshot("pilot-c")
            assert current == observed["production_current_after"]
            assert production.snapshot(current)["commit_sha"] == (
                merged["source_revision"])
            edge_id = (f"edge:CALLS:{nodes['source']['id']}:"
                       f"{nodes['sink']['id']}")
            clang = [row for row in production.support_receipts(
                "pilot-c", current, edge_id) if row["lane_id"] == "clang_provider"]
            assert len(clang) == 1
            assert (clang[0]["truth_class"], clang[0]["execution_modality"],
                    clang[0]["target_resolution"], clang[0]["coverage"]) == (
                        "OBSERVED", "MAY", "EXACT", "UNKNOWN")
        finally:
            production.close()
    get_settings.cache_clear()
