"""Fresh local MCP contract over a real indexed Python+C repository."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import Request, urlopen

import pytest

from rintel.db import Database
from rintel.indexer import Indexer


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def indexed_local(tmp_path: Path):
    repo = tmp_path / "real-repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def beta():\n    return 1\n\ndef alpha():\n    return beta()\n")
    (repo / "sample.c").write_text(
        "int twice(int x) { return x * 2; }\n"
        "int main(void) { return twice(2); }\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "witness"],
                   check=True)
    data_home = tmp_path / "rintel-data"
    data_home.mkdir()
    db = Database(data_home / "evidence.db")
    Indexer(db, repo, repo_id="witness").index()
    sid = db.current_snapshot("witness")
    assert sid is not None
    nodes = {n["name"]: n for n in db.all_nodes("witness", sid)
             if n["name"] in {"alpha", "twice"}}
    assert set(nodes) == {"alpha", "twice"}
    assert any(e["kind"] == "CALLS" for e in db.all_edges("witness", sid))
    db.close()
    yield data_home, repo, sid, nodes


class MCPProcess:
    def __init__(self, data_home: Path, *, preset: str | None = None,
                 exposure: str | None = None):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("RINTEL_DATABASE_URL", None)
        env.pop("RINTEL_SQLITE_PATH", None)
        env.pop("RINTEL_MCP_TOOLS", None)
        if exposure is not None:
            env["RINTEL_MCP_TOOLS"] = exposure
        env["RINTEL_DATA_HOME"] = str(data_home)
        command = [str(ROOT / "rintel"), "mcp"]
        if preset:
            command += ["--preset", preset]
        self.proc = subprocess.Popen(command, cwd=ROOT, env=env, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.next_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id,
                                          "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read() if self.proc.stderr else "MCP closed"
        return json.loads(line)

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        response = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        assert "error" not in response, response
        return json.loads(response["result"]["content"][0]["text"])

    def close(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=30)
        assert self.proc.returncode == 0, self.proc.stderr.read() if self.proc.stderr else ""


def test_read_preset_and_real_published_drilldown(indexed_local):
    data_home, repo, sid, nodes = indexed_local
    mcp = MCPProcess(data_home)
    try:
        initialized = mcp.call("initialize")["result"]
        instructions = initialized["instructions"]
        for boundary in ("UNKNOWN != FALSE", "PARTIAL != COMPLETE",
                         "NOT_OBSERVED", "Runtime OBSERVED", "NOT Canonical Evidence",
                         "Agent claim", "Adjacency"):
            assert boundary.lower() in instructions.lower()
        names = {t["name"] for t in mcp.call("tools/list")["result"]["tools"]}
        assert {"repo_status", "search_symbols", "get_symbol", "query_topology",
                "explain_evidence", "read_resource"} <= names
        assert "create_design" not in names
        denied = mcp.tool("create_design", {"name": "nope"})
        assert denied["error"]["code"] == "TOOL_NOT_EXPOSED"
        status = mcp.tool("repo_status")
        published = next(r for r in status["repos"] if r["repo_id"] == "witness")
        assert published["snapshot_id"] == sid
        assert "evidence_revision" not in status  # mixed/multiple repos have per-row revisions
        for node in nodes.values():
            matches = mcp.tool("search_symbols", {"query": node["name"],
                                                   "repo_id": "witness"})["matches"]
            assert any(row["canonical_id"] == node["id"] for row in matches)
            symbol = mcp.tool("get_symbol", {"canonical_id": node["id"],
                                             "repo_id": "witness"})["hits"][0]
            assert symbol["identity"]["span"]["path"] == node["path"]
            topology = mcp.tool("query_topology", {"repo_id": "witness",
                                                    "root": node["id"]})
            assert topology["snapshot_id"] == sid
            assert topology["evidence_revision"] == sid
            assert any(e["kind"] == "CALLS" for e in topology["edges"])
            evidence = mcp.tool("explain_evidence", {"entity_id": node["id"],
                                                         "repo_id": "witness"})
            assert evidence["snapshot_id"] == sid
            assert evidence["direct_record_found"]
            source = mcp.tool("read_resource", {"uri": symbol["source_uri"]})
            assert node["name"] in source["text"]
            assert source["snapshot_id"] == sid
        main = next(row for row in mcp.tool("search_symbols", {
            "query": "main", "repo_id": "witness"})["matches"]
                    if row["name"] == "main")
        path = mcp.tool("find_path", {"repo_id": "witness",
                                      "source": main["canonical_id"],
                                      "target": nodes["twice"]["id"]})
        assert path["verdict"] == "FOUND"
        assert path["paths"][0]["hops"][0]["edge_id"].startswith("edge:CALLS:")
        for tool in ("query_flow", "query_runtime", "query_data_interface"):
            assert isinstance(mcp.tool(tool), dict)
        forbidden = mcp.tool("read_resource", {"uri": "rintel://file//etc/passwd"})
        assert forbidden["error"]["code"] == "INVALID_URI"
    finally:
        mcp.close()


def test_empty_store_and_invalid_arguments_fail_closed(tmp_path: Path):
    data_home = tmp_path / "empty"
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("CANARY_OUTSIDE_REGISTERED_REPOSITORY")
    mcp = MCPProcess(data_home)
    try:
        assert mcp.tool("repo_status")["repos"] == []
        missing_status = mcp.tool("repo_status", {"repo": "absent"})
        assert missing_status["error"]["code"] == "REPO_NOT_FOUND"
        assert "evidence_revision" not in missing_status
        missing = mcp.tool("search_symbols", {"query": "x", "repo_id": "absent"})
        assert missing["error"]["code"] == "REPO_NOT_FOUND"
        missing_evidence = mcp.tool("explain_evidence", {
            "entity_id": "node:absent", "repo_id": "absent"})
        assert missing_evidence["error"]["code"] == "REPO_NOT_FOUND"
        bad = mcp.tool("get_symbol", {"canonical_id": 42})
        assert bad["error"]["code"] == "INVALID_ARGUMENT_TYPE"
        malformed_args = mcp.call("tools/call", {"name": "get_symbol", "arguments": [1]})
        assert "error" not in malformed_args
        assert json.loads(malformed_args["result"]["content"][0]["text"])["error"]["code"] == "INVALID_ARGUMENT_TYPE"
        uri = mcp.tool("read_resource", {"uri": "rintel://unknown"})
        assert uri["error"]["code"] == "INVALID_URI"
        for disguised in (f"x/rintel://file/{outside}",
                          f"rintel://ignored/rintel://file/{outside}",
                          f"\nrintel://file/{outside}",
                          f"rintel://\nfile/{outside}", "rintel://["):
            denied = mcp.tool("read_resource", {"uri": disguised})
            assert denied["error"]["code"] == "INVALID_URI"
            assert "CANARY_OUTSIDE_REGISTERED_REPOSITORY" not in json.dumps(denied)
            resource_response = mcp.call("resources/read", {"uri": disguised})
            resource_denied = json.loads(resource_response["result"]["contents"][0]["text"])
            assert resource_denied["error"]["code"] == "INVALID_URI"
            assert "CANARY_OUTSIDE_REGISTERED_REPOSITORY" not in json.dumps(resource_denied)
    finally:
        mcp.close()


def test_unindexed_repo_stale_source_and_restart(indexed_local):
    data_home, repo, sid, nodes = indexed_local
    db = Database(data_home / "evidence.db")
    db.upsert_repo("not-indexed", str(repo))
    db.close()
    mcp = MCPProcess(data_home)
    try:
        status = mcp.tool("repo_status")
        row = next(r for r in status["repos"] if r["repo_id"] == "not-indexed")
        assert row["status"] == "UNINDEXED"
        absent = mcp.tool("query_topology", {"repo_id": "not-indexed",
                                                  "root": nodes["alpha"]["id"]})
        assert absent["error"]["code"] == "REPO_NOT_INDEXED"
        absent_search = mcp.tool("search_symbols", {"repo_id": "not-indexed",
                                                     "query": "alpha"})
        assert absent_search["error"]["code"] == "REPO_NOT_INDEXED"
        absent_evidence = mcp.tool("explain_evidence", {
            "entity_id": nodes["alpha"]["id"], "repo_id": "not-indexed"})
        assert absent_evidence["error"]["code"] == "REPO_NOT_INDEXED"
        symbol = mcp.tool("get_symbol", {"repo_id": "witness",
                                         "canonical_id": nodes["alpha"]["id"]})["hits"][0]
        (repo / "sample.py").write_text("def alpha():\n    return 999\n")
        stale = mcp.tool("read_resource", {"uri": symbol["source_uri"]})
        assert stale["error"]["code"] == "SOURCE_STALE"
        (repo / "sample.py").unlink()
        missing_source = mcp.tool("read_resource", {"uri": symbol["source_uri"]})
        assert missing_source["error"]["code"] == "SOURCE_UNAVAILABLE"
    finally:
        mcp.close()
    restarted = MCPProcess(data_home)
    try:
        status = restarted.tool("repo_status")
        assert any(r["repo_id"] == "witness" and r["snapshot_id"] == sid
                   for r in status["repos"])
    finally:
        restarted.close()


def test_doctor_reports_real_mcp_runtime(tmp_path: Path):
    env = os.environ.copy()
    env["RINTEL_DATA_HOME"] = str(tmp_path / "doctor-data")
    env.pop("RINTEL_DATABASE_URL", None)
    env.pop("RINTEL_SQLITE_PATH", None)
    result = subprocess.run([str(ROOT / "rintel"), "doctor", "--json"],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=60, check=True)
    report = json.loads(result.stdout)
    assert report["mcp"]["status"] == "READY"
    assert report["mcp"]["datastore_kind"] == "SQLite"
    assert report["mcp"]["datastore"] == str(tmp_path / "doctor-data" / "evidence.db")
    assert report["mcp"]["instructions"] == "loaded"
    assert "repo_status" in report["mcp"]["tool_surface"]


def test_installed_cli_mcp_entrypoint(tmp_path: Path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("RINTEL_DATABASE_URL", None)
    env.pop("RINTEL_SQLITE_PATH", None)
    env["RINTEL_DATA_HOME"] = str(tmp_path / "installed-cli-data")
    result = subprocess.run([str(ROOT / ".venv" / "bin" / "rintel"), "mcp"],
                            cwd=ROOT, env=env,
                            input=json.dumps({"jsonrpc": "2.0", "id": 1,
                                              "method": "initialize"}) + "\n",
                            capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout)["result"]["instructions"]
    assert (tmp_path / "installed-cli-data" / "evidence.db").is_file()


def test_default_local_datastore_path(tmp_path: Path):
    env = os.environ.copy()
    for key in ("PYTHONPATH", "RINTEL_DATA_HOME", "RINTEL_DATABASE_URL",
                "RINTEL_SQLITE_PATH", "XDG_DATA_HOME"):
        env.pop(key, None)
    env["HOME"] = str(tmp_path)
    if sys.platform == "darwin":
        expected = tmp_path / "Library" / "Application Support" / "Rintel" / "evidence.db"
    else:
        env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
        expected = tmp_path / "xdg" / "rintel" / "evidence.db"
    result = subprocess.run([str(ROOT / "rintel"), "doctor", "--json"],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=60, check=True)
    report = json.loads(result.stdout)
    assert report["mcp"]["status"] == "READY"
    assert report["mcp"]["datastore"] == str(expected)
    assert expected.is_file()


def test_design_preset_only_explicitly_exposes_bounded_tools(tmp_path: Path):
    mcp = MCPProcess(tmp_path / "design", preset="design-execute")
    try:
        names = {t["name"] for t in mcp.call("tools/list")["result"]["tools"]}
        assert "create_design" in names
        assert "mutate_flow_design" in names
        denied = mcp.tool("mutate_flow_design", {
            "change_id": "change-unknown", "stable_id": "flow-unknown",
            "operation": "add_block", "ops": [{"flow_id": "flow-unknown"}]})
        assert denied["error"]["code"] == "MISSING_REQUIRED_ARGUMENT"
        assert "request_agent_execution" in names
        assert "publish_canonical" not in names
        assert "shell" not in names
    finally:
        mcp.close()


def test_custom_exposure_remains_authoritative_for_resources(tmp_path: Path):
    mcp = MCPProcess(tmp_path / "custom", exposure="repo_status")
    try:
        names = {t["name"] for t in mcp.call("tools/list")["result"]["tools"]}
        assert names == {"repo_status"}
        assert mcp.call("resources/list")["result"]["resources"] == []
        result = mcp.call("resources/read", {"uri": "rintel://file//etc/passwd"})
        denied = json.loads(result["result"]["contents"][0]["text"])
        assert denied["error"]["code"] == "TOOL_NOT_EXPOSED"
    finally:
        mcp.close()


def test_real_http_and_mcp_see_same_snapshot_and_span(indexed_local):
    data_home, _repo, sid, nodes = indexed_local
    env = os.environ.copy()
    for key in ("PYTHONPATH", "RINTEL_DATABASE_URL", "RINTEL_SQLITE_PATH"):
        env.pop(key, None)
    env["RINTEL_DATA_HOME"] = str(data_home)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([str(ROOT / "rintel"), "serve", "--no-browser",
                               "--port", str(port)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True)
    mcp = MCPProcess(data_home)
    try:
        url = f"http://127.0.0.1:{port}/api/v1"
        for _ in range(100):
            try:
                with urlopen(f"{url}/repos/witness", timeout=1) as response:
                    http_repo = json.load(response)
                break
            except OSError:
                if server.poll() is not None:
                    pytest.fail(f"server exited: {server.stderr.read()}")
                time.sleep(0.1)
        else:
            pytest.fail("HTTP server did not become ready")
        mcp_repo = next(r for r in mcp.tool("repo_status")["repos"]
                        if r["repo_id"] == "witness")
        assert http_repo["id"] == mcp_repo["repo_id"]
        assert http_repo["latest_snapshot"] == mcp_repo["snapshot_id"] == sid
        node = nodes["alpha"]
        with urlopen(f"{url}/search?repo_id=witness&q=alpha", timeout=5) as response:
            http_symbol = next(r for r in json.load(response)["items"]
                               if r["id"] == node["id"])
        mcp_symbol = mcp.tool("get_symbol", {"canonical_id": node["id"],
                                              "repo_id": "witness"})["hits"][0]
        assert http_symbol["path"] == mcp_symbol["identity"]["file"]
        assert http_symbol["start_line"] == mcp_symbol["identity"]["span"]["start_line"]
        with urlopen(f"{url}/evidence?repo_id=witness&entity_type=node&entity_id={node['id']}",
                     timeout=5) as response:
            http_evidence = json.load(response)
        mcp_evidence = mcp.tool("explain_evidence", {"repo_id": "witness",
                                                        "entity_id": node["id"]})
        assert http_evidence["total"] == len(mcp_evidence["evidence"])
        assert http_evidence["items"][0]["location"] == mcp_evidence["evidence"][0]["location"]
        with urlopen(f"{url}/source?repo_id=witness&path=sample.py"
                     f"&start={node['start_line']}&end={node['end_line']}",
                     timeout=5) as response:
            http_source = json.load(response)
        mcp_source = mcp.tool("read_resource", {"uri": mcp_symbol["source_uri"]})
        assert http_source["snapshot"] == mcp_source["snapshot_id"]
        assert http_source["content"].splitlines()[0] == mcp_source["text"].splitlines()[0]
    finally:
        mcp.close()
        server.terminate()
        server.wait(timeout=20)


def test_fresh_http_registration_and_index_are_immediately_visible_to_mcp(tmp_path: Path):
    repo = tmp_path / "fresh-git-repo"
    repo.mkdir()
    (repo / "fresh.py").write_text("def fresh_python():\n    return 7\n")
    (repo / "fresh.c").write_text("int fresh_c(void) { return 7; }\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "fresh"],
                   check=True)
    data_home = tmp_path / "fresh-data"
    env = os.environ.copy()
    for key in ("PYTHONPATH", "RINTEL_DATABASE_URL", "RINTEL_SQLITE_PATH"):
        env.pop(key, None)
    env["RINTEL_DATA_HOME"] = str(data_home)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([str(ROOT / "rintel"), "serve", "--no-browser",
                               "--port", str(port)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True)
    mcp = MCPProcess(data_home)
    try:
        assert mcp.tool("repo_status")["repos"] == []
        url = f"http://127.0.0.1:{port}/api/v1"
        for _ in range(100):
            try:
                with urlopen(f"{url}/repos", timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    pytest.fail(f"server exited: {server.stderr.read()}")
                time.sleep(0.1)
        else:
            pytest.fail("HTTP server did not become ready")
        request = Request(f"{url}/repos", data=json.dumps({
            "root_path": str(repo), "label": "fresh"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            job_id = json.load(response)["job_id"]
        snapshot = None
        for _ in range(200):
            try:
                with urlopen(f"{url}/repos/fresh", timeout=5) as response:
                    snapshot = json.load(response)["latest_snapshot"]
            except OSError:
                pass
            if snapshot:
                break
            time.sleep(0.1)
        assert snapshot, f"index job {job_id} did not publish a snapshot"
        status = mcp.tool("repo_status")
        published = next(r for r in status["repos"] if r["repo_id"] == "fresh")
        assert published["snapshot_id"] == snapshot
        for name in ("fresh_python", "fresh_c"):
            match = next(r for r in mcp.tool("search_symbols", {
                "query": name, "repo_id": "fresh"})["matches"] if r["name"] == name)
            symbol = mcp.tool("get_symbol", {"canonical_id": match["canonical_id"],
                                             "repo_id": "fresh"})["hits"][0]
            evidence = mcp.tool("explain_evidence", {"entity_id": match["canonical_id"],
                                                     "repo_id": "fresh"})
            source = mcp.tool("read_resource", {"uri": symbol["source_uri"]})
            assert evidence["direct_record_found"]
            assert name in source["text"]
    finally:
        mcp.close()
        server.terminate()
        server.wait(timeout=20)
