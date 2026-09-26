"""Focused regressions found by the bounded CORE-LOOP0 pilot."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rintel.db import Database
from rintel.indexer import Indexer
from rintel.lvs.flow_runner import _edges_for
from rintel.server.jobs import make_index_runner


class _SupportStore:
    def __init__(self, receipt: dict):
        self.receipt = receipt

    def all_edges(self, _repo: str, _sid: str) -> list[dict]:
        return [{"id": "edge:CALLS:a:b", "kind": "CALLS",
                 "src_id": "a", "dst_id": "b"}]

    def support_receipts(self, _repo: str, _sid: str,
                         _fact_id: str) -> list[dict]:
        return [self.receipt]


def test_lvs_does_not_promote_legacy_candidate_to_exact():
    candidate = {"support_receipt_id": "support:candidate",
                 "truth_class": "INFERRED", "execution_modality": "MAY",
                 "target_resolution": "CANDIDATE_SET", "coverage": "UNKNOWN"}
    projected = _edges_for(_SupportStore(candidate), "r", "s")[0]
    assert projected["kind"] == "CALL"
    assert projected["truth_class"] == "INFERRED"
    assert projected["target_resolution"] != "EXACT"
    assert projected["coverage"] != "COMPLETE"
    assert projected["representative_witnesses"] == [
        {"support_receipt_id": "support:candidate"}]

    exact = {"support_receipt_id": "support:exact",
             "truth_class": "OBSERVED", "execution_modality": "MUST",
             "target_resolution": "EXACT", "coverage": "COMPLETE"}
    hard = _edges_for(_SupportStore(exact), "r", "s")[0]
    assert hard["target_resolution"] == "EXACT"
    assert hard["coverage"] == "COMPLETE"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Pilot", "-c",
         "user.email=pilot@example.invalid", "-C", str(repo), *args],
        text=True, capture_output=True, check=True)
    return result.stdout.strip()


def test_linked_reindex_preserves_bound_git_identity(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "add", "sample.py")
    _git(repo, "commit", "-qm", "initial")
    commit = _git(repo, "rev-parse", "HEAD")
    path = tmp_path / "store.sqlite"
    db = Database(path)
    first = Indexer(db, repo, repo_id="r", commit=commit).index()
    db.close()

    runner = make_index_runner(lambda: Database(path), "r", commit=commit)
    result = runner({}, lambda _pct, _phase: None)
    db = Database(path)
    try:
        assert db.snapshot(result["snapshot_id"])["commit_sha"] == commit
        assert db.current_snapshot("r") == result["snapshot_id"]
        assert db.snapshot(first.snapshot_id)["commit_sha"] == commit
    finally:
        db.close()

    (repo / "sample.py").write_text("def f():\n    return 2\n")
    with pytest.raises(RuntimeError, match="clean bound Git commit"):
        runner({}, lambda _pct, _phase: None)
