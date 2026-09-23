"""Frozen-artifact + source reader for flow inference (FLOW-INFER0 §5-§8).

Reads ONLY:
  - analysis_tournament/topology/fac_c.topology.json   (frozen, TOPO-ENGINE0)
  - the real FAC source tree (read-only, for entry/dispatch/guard text)

The reader keeps canonical ids verbatim; unresolved call targets carry the
call-site expression name from the witness (honest: name from source,
identity unresolved).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOURNAMENT = REPO_ROOT / "analysis_tournament"

APPS = {
    "fac": {"file": "sfac/sfac.c", "init": "InitFac0", "mod": "fac"},
    "crm": {"file": "sfac/scrm.c", "init": "InitCRM0", "mod": "crm"},
    "spol": {"file": "sfac/spol.c", "init": "InitPolarization", "mod": "spol"},
}

METHOD_TABLE_RE = re.compile(r'^[ \t]*\{"([^"]+)",\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,}]')
IF_RE = re.compile(r'^\s*if\s*\((.*)\)\s*\{?', re.M | re.I)
SWITCH_RE = re.compile(r'^\s*switch\s*\((.*)\)\s*\{?', re.M | re.I)
CASE_RE = re.compile(r'^\s*case\s+([^:]+):', re.M | re.I)


class FacFacts:
    """Index over the frozen FAC lane bundle."""

    def __init__(self, bundle: dict, source_root: Path, extra: dict | None = None):
        self.bundle = bundle
        self.source_root = source_root
        # the raw artifact nests everything under "topology"; the projected
        # bundle (build_lane_bundle) is flat — accept both (read-only).
        topo = dict(bundle.get("topology") or bundle)
        self.meta = bundle.get("meta") or {}
        if extra:
            from .topology_ext import merge_topologies
            merged = merge_topologies(
                {"nodes": topo.get("nodes") or [], "edges": topo.get("edges") or []},
                {"nodes": extra.get("nodes") or [], "edges": extra.get("edges") or []})
            topo["nodes"] = merged["nodes"]
            topo["edges"] = merged["edges"]
        self.nodes: list[dict] = list(topo.get("nodes") or [])
        self.edges: list[dict] = list(topo.get("edges") or [])
        # LC2: an EXACT edge outranks a stale UNKNOWN edge for the same
        # call-site expression (coverage expansion evidence — the kernel call
        # graph resolves the same source call the frozen lane left unknown;
        # keeping both would make region callee resolution non-deterministic).
        # NOTE: frozen joern edges can carry target "[Unknown Dynamic Target]"
        # WITH target_resolution "EXACT" — classify by the target VALUE.
        exact = set()
        for e in self.edges:
            tgt = e.get("target") or ""
            if e.get("target_resolution") == "EXACT" and tgt != "[Unknown Dynamic Target]":
                w = (e.get("representative_witnesses") or [e])[0]
                exact.add((src_name(e), w.get("expr") or tgt or ""))
        self.edges = [e for e in self.edges
                      if (e.get("target") or "") != "[Unknown Dynamic Target]"
                      or (src_name(e), ((e.get("representative_witnesses") or [e])[0]
                                        .get("expr") or e.get("target") or "")) not in exact]
        self.symbols: dict[str, list[dict]] = defaultdict(list)   # name -> nodes (id collisions live)
        self.by_id: dict[str, dict] = {}
        for n in self.nodes:
            key = id_of(n).split(":")[-1] or (n.get("name") or "")
            self.symbols[key].append(n)
            self.by_id[n["canonical_symbol_id"]] = n
        self._callees: dict[str, list[dict]] = defaultdict(list)  # src name -> edges (kind=CALL)
        self._callers: dict[str, list[dict]] = defaultdict(list)
        for e in self.edges:
            if e.get("kind") != "CALL":
                continue
            self._callees[src_name(e)].append(e)
            tgt = e.get("target") or ""
            if tgt != "[Unknown Dynamic Target]":
                self._callers[tgt.split(":")[-1]].append(e)

    # -- browse helpers ------------------------------------------------------
    def node_of(self, name: str) -> dict | None:
        vals = self.symbols.get(name) or []
        return vals[0] if vals else None

    def callee_edges(self, src_name: str) -> list[dict]:
        return self._callees.get(src_name) or []

    def callee_names(self, src_name: str) -> list[str]:
        """Resolved callees + call-expr names of unresolved targets, deduped."""
        out: list[str] = []
        for e in self.callee_edges(src_name):
            tgt = e.get("target") or ""
            if tgt == "[Unknown Dynamic Target]":
                for w in e.get("representative_witnesses") or []:
                    nm = w.get("expr") or last_fact(w.get("fact_id") or "")
                    if nm and nm not in out:
                        out.append(nm)
            else:
                nm = tgt.split(":")[-1]
                if nm not in out:
                    out.append(nm)
        return out

    def unknown_call_facts(self, src_name: str) -> list[dict]:
        out = []
        for e in self.callee_edges(src_name):
            if e.get("target") == "[Unknown Dynamic Target]":
                out.extend(e.get("representative_witnesses") or [e])
        return out

    # -- source access -------------------------------------------------------
    def source_lines(self, rel: str) -> list[str]:
        p = self.source_root / rel
        try:
            return p.read_text(errors="replace").splitlines()
        except OSError:
            return []

    def read_file(self, rel: str) -> str:
        try:
            return (self.source_root / rel).read_text(errors="replace")
        except OSError:
            return ""

    def method_tables(self) -> dict[str, list[tuple[str, str, int]]]:
        """file -> [(method_name, handler, line_index)] from the frozen source."""
        out: dict[str, list[tuple[str, str, int]]] = {}
        for rel in {a["file"] for a in APPS.values() if a}:
            methods: list[tuple[str, str, int]] = []
            for i, line in enumerate(self.source_lines(rel)):
                m = METHOD_TABLE_RE.match(line)
                if m:
                    methods.append((m.group(1), m.group(2), i))
            if methods:
                out[rel] = methods
        return out

    def guard_sites(self, rel: str) -> list[dict]:
        """if/switch conditions with source line (inputs, never fabricated)."""
        out: list[dict] = []
        for i, line in enumerate(self.source_lines(rel)):
            m = IF_RE.match(line)
            if m:
                out.append({"line": i + 1, "kind": "if", "expression": m.group(1).strip()})
                continue
            m = SWITCH_RE.match(line)
            if m:
                out.append({"line": i + 1, "kind": "switch", "expression": m.group(1).strip()})
                continue
            m = CASE_RE.match(line)
            if m:
                out.append({"line": i + 1, "kind": "case", "expression": m.group(1).strip()})
        return out


def id_of(n: dict) -> str:
    return n.get("canonical_symbol_id") or ""


def src_name(e: dict) -> str:
    return (e.get("source") or "").split(":")[-1]


def last_fact(fid: str) -> str:
    return (fid or "").split(":")[-1]


def load_fac_bundle(extra: dict | None = None) -> dict:
    p = TOURNAMENT / "topology" / "fac_c.topology.json"
    return json.loads(p.read_text())


def fac_source_root() -> Path:
    return Path("/Users/wu/Documents/dh/a3/fac/fac")
