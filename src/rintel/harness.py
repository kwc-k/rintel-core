"""Correctness harness (spec §31, §43.6/43.7).

Runs the indexer over a T0 fixture, compares the extracted graph against the
fixture's ground truth, and measures:
  node precision / recall          edge precision / recall
  source-location accuracy         cross-file identity (edges across files)
  parse success rate               incremental consistency (via tests)
Raw per-run results are appended as JSONL to results/ (never only aggregates).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .indexer import Indexer


@dataclass
class FixtureMetrics:
    language: str = ""
    fixture: str = ""
    run_id: str = ""
    snapshot_id: str = ""
    node_tp: int = 0
    node_fp: int = 0
    node_fn: int = 0
    edge_tp: int = 0
    edge_fp: int = 0
    edge_fn: int = 0
    location_checked: int = 0
    location_hits: int = 0
    cross_file_expected: int = 0
    cross_file_resolved: int = 0
    parse_success: float = 1.0
    duration_ms: int = 0
    files_total: int = 0
    files_parsed: int = 0
    callsites_total: int = 0
    callsites_resolved: int = 0
    callsites_unresolved: int = 0
    unresolved_notes: int = 0
    node_kinds: dict = field(default_factory=dict)
    edge_kinds: dict = field(default_factory=dict)
    languages: dict = field(default_factory=dict)
    matched_nodes: list = field(default_factory=list)
    matched_edges: list = field(default_factory=list)
    misses_nodes: list = field(default_factory=list)
    misses_edges: list = field(default_factory=list)

    @property
    def node_precision(self) -> float:
        d = self.node_tp + self.node_fp
        return self.node_tp / d if d else 1.0

    @property
    def node_recall(self) -> float:
        d = self.node_tp + self.node_fn
        return self.node_tp / d if d else 1.0

    @property
    def edge_precision(self) -> float:
        d = self.edge_tp + self.edge_fp
        return self.edge_tp / d if d else 1.0

    @property
    def edge_recall(self) -> float:
        d = self.edge_tp + self.edge_fn
        return self.edge_tp / d if d else 1.0

    @property
    def location_accuracy(self) -> float:
        return self.location_hits / self.location_checked if self.location_checked else 1.0

    @property
    def cross_file_identity(self) -> float:
        return self.cross_file_resolved / self.cross_file_expected if self.cross_file_expected else 1.0

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "language", "fixture", "run_id", "snapshot_id",
            "node_tp", "node_fp", "node_fn", "edge_tp", "edge_fp", "edge_fn",
            "location_checked", "location_hits", "cross_file_expected",
            "cross_file_resolved", "parse_success", "duration_ms",
            "files_total", "files_parsed", "callsites_total",
            "callsites_resolved", "callsites_unresolved", "unresolved_notes",
            "node_kinds", "edge_kinds", "languages")}
        d["node_precision"] = self.node_precision
        d["node_recall"] = self.node_recall
        d["edge_precision"] = self.edge_precision
        d["edge_recall"] = self.edge_recall
        d["location_accuracy"] = self.location_accuracy
        d["cross_file_identity"] = self.cross_file_identity
        return d


def verify_fixture(fixture_dir: str | Path, db_path: str | None = None,
                   line_tolerance: int = 1,
                   store_factory=None) -> FixtureMetrics:
    """Run the indexer over a T0 fixture and compare against ground truth.

    `store_factory` (optional, SPEC-P1 §21): zero-arg callable returning a
    `Store` (e.g. a PgStore on an isolated schema) so the same matching logic
    verifies both backends.  Defaults to a throwaway SQLite `Database`.
    """
    fixture_dir = Path(fixture_dir)
    gt_path = fixture_dir / "ground_truth.json"
    if not gt_path.exists():
        raise FileNotFoundError(f"no ground_truth.json in {fixture_dir}")
    gt = json.loads(gt_path.read_text())
    t0 = time.time()
    if store_factory is not None:
        db = store_factory()
    else:
        dbp = db_path or os.path.join(tempfile.mkdtemp(prefix="rintel-"),
                                      "ev.db")
        db = Database(dbp)
    idx = Indexer(db, str(fixture_dir), repo_id=fixture_dir.name)
    res = idx.index()
    sid = res.snapshot_id
    repo = idx.repo_id
    nodes = db.all_nodes(repo, sid)
    edges = db.all_edges(repo, sid)
    stats = db.stats(repo, sid)
    m = FixtureMetrics(language=gt.get("language", "?"),
                       fixture=fixture_dir.name,
                       run_id=res.run_id, snapshot_id=sid)
    m.duration_ms = int((time.time() - t0) * 1000)
    m.files_total = res.files_total
    m.files_parsed = res.files_parsed
    m.callsites_total = stats["callsites"]
    m.callsites_unresolved = stats["callsites_unresolved"]
    m.callsites_resolved = stats["callsites"] - stats["callsites_unresolved"]
    m.unresolved_notes = stats["unresolved_notes"]
    m.node_kinds = stats["node_kinds"]
    m.edge_kinds = stats["edge_kinds"]
    m.languages = stats["languages"]
    if res.files_total:
        m.parse_success = (res.files_total - res.files_failed) / res.files_total

    # ---- node matching -------------------------------------------------
    # structural containment nodes (REPOSITORY / DIRECTORY / FILE) are
    # verified through edges; symbol precision/recall covers the rest
    SKIP_KINDS = {"REPOSITORY", "DIRECTORY", "FILE"}
    actual: dict[tuple, dict] = {}
    for n in nodes:
        if n["kind"] in SKIP_KINDS:
            continue
        actual[(n["kind"], n["name"], n["path"])] = n
    expected_nodes = [(e["kind"], e["name"], e["path"])
                      for e in gt.get("nodes", [])]
    for exp in expected_nodes:
        if exp in actual:
            m.node_tp += 1
            m.matched_nodes.append(list(exp))
            a = actual[exp]
            if a.get("start_line"):
                m.location_checked += 1
                if abs(a["start_line"] - _gt_line(gt, exp)) <= line_tolerance:
                    m.location_hits += 1
        else:
            m.node_fn += 1
            m.misses_nodes.append(list(exp))
    m.node_fp = len(actual) - m.node_tp

    # ---- edge matching -------------------------------------------------
    # ground truth entries with explicit src_path/dst_path match exactly;
    # entries without paths match loosely on (kind, src_name, dst_name)
    exp_exact: dict[tuple, dict] = {}
    exp_loose: dict[tuple, dict] = {}
    for e in gt.get("edges", []):
        if "src_path" in e or "dst_path" in e:
            exp_exact[(e["kind"], e["src"], e["dst"], e.get("src_path"),
                       e.get("dst_path"))] = e
        else:
            exp_loose[(e["kind"], e["src"], e["dst"])] = e
    by_id = {n["id"]: n for n in nodes}
    STRUCTURAL = {"REPOSITORY", "DIRECTORY", "FILE"}
    seen: set[tuple] = set()
    for e in edges:
        s, d = by_id.get(e["src_id"]), by_id.get(e["dst_id"])
        if s is None or d is None:
            continue
        if e["kind"] == "CONTAINS" and (s["kind"] in STRUCTURAL or
                                        d["kind"] in STRUCTURAL):
            continue  # tree containment, verified structurally
        key5 = (e["kind"], s["name"], d["name"], s["path"], d["path"])
        key3 = (e["kind"], s["name"], d["name"])
        matched_key = None
        if key5 in exp_exact:
            matched_key = key5
        elif key3 in exp_loose:
            matched_key = key3
        if matched_key is not None:
            m.edge_tp += 1
            seen.add(matched_key)
            m.matched_edges.append([matched_key[0], matched_key[1],
                                    matched_key[2]])
            if s["path"] != d["path"]:
                m.cross_file_resolved += 1
        else:
            m.edge_fp += 1
            m.misses_edges.append([key3[0], key3[1], key3[2],
                                   s["path"], d["path"]])
    for key in exp_exact:
        if key not in seen:
            m.edge_fn += 1
    for key in exp_loose:
        if key not in seen:
            m.edge_fn += 1
    m.cross_file_expected = sum(
        1 for e in gt.get("edges", [])
        if e.get("src_path") and e.get("dst_path")
        and e["src_path"] != e["dst_path"])
    db.close()
    return m


def _gt_line(gt: dict, exp: tuple) -> int:
    for e in gt.get("nodes", []):
        if (e["kind"], e["name"], e["path"]) == exp:
            return e.get("line", 0)
    return 0


def append_jsonl(metrics: FixtureMetrics, out_dir: str | Path,
                 run_label: str = "") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"verify-{stamp}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        row = metrics.as_dict()
        row["run_label"] = run_label
        f.write(json.dumps(row) + "\n")
    return path


def print_table(metrics: list[FixtureMetrics]) -> None:
    hdr = (f"{'fixture':<16}{'lang':<12}{'nP':>7}{'nR':>7}{'eP':>7}{'eR':>7}"
           f"{'loc':>7}{'xf':>7}{'parse':>7}{'unres':>6}{'ms':>7}")
    print(hdr)
    print("-" * len(hdr))
    for m in metrics:
        print(f"{m.fixture:<16}{m.language:<12}"
              f"{m.node_precision:>7.3f}{m.node_recall:>7.3f}"
              f"{m.edge_precision:>7.3f}{m.edge_recall:>7.3f}"
              f"{m.location_accuracy:>7.3f}{m.cross_file_identity:>7.3f}"
              f"{m.parse_success:>7.3f}{m.callsites_unresolved:>6}"
              f"{m.duration_ms:>7}")
