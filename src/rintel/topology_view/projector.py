"""Frozen-artifact reader + renderer-neutral view model (TOPO-UI0, §1).

Read-only consumption of upstream outputs:

    analysis_tournament/topology/{lane}.topology.json       (TOPO-ENGINE0)
    analysis_tournament/module_infer/{lane}.candidates.json (MODULE-INFER0)
    analysis_tournament/module_opt/route_b.json             (MODULE-OPT0, JPL)

Nothing here re-analyzes code, mutates topology, or upgrades truth.  The
capability language is inherited verbatim from the upstream artifacts
(e.g. data_capability=PARTIAL) and is surfaced to the UI unchanged.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOURNAMENT = REPO_ROOT / "analysis_tournament"

RELATION_KINDS = ("CALL", "DATA", "STATE", "CONTROL", "TIME", "RESOURCE")

_PRUNE_DIRS = {"node_modules", ".venv", ".venv312", ".venv39", ".git",
               "dist", "__pycache__", ".pytest_cache", "test-results",
               ".next", "build"}


class LaneSpec:
    def __init__(self, lane: str, label: str, languages: list[str],
                 source_repo_id: str | None, source_root: str | None,
                 data_capability: str, notes: list[str],
                 synthetic: bool = False):
        self.lane = lane
        self.label = label
        self.languages = languages
        self.source_repo_id = source_repo_id
        self.source_root = source_root
        self.data_capability = data_capability
        self.notes = notes
        self.synthetic = synthetic

    @property
    def topology_path(self) -> Path:
        return TOURNAMENT / "topology" / f"{self.lane}.topology.json"

    @property
    def candidates_path(self) -> Path:
        # FAC lane: upstream MODULE-INFER0 artifact is named after the
        # analyzed facet ("fac.candidates.json"), not the lane id.
        if self.lane == "fac_c":
            alt = TOURNAMENT / "module_infer" / "fac_c.candidates.json"
            if alt.exists():
                return alt
            return TOURNAMENT / "module_infer" / "fac.candidates.json"
        return TOURNAMENT / "module_infer" / f"{self.lane}.candidates.json"

    @property
    def route_b_path(self) -> Path:
        return TOURNAMENT / "module_opt" / "route_b.json"

    @property
    def route_c_path(self) -> Path:
        return TOURNAMENT / "module_opt" / "route_c.json"

    def available(self) -> bool:
        return self.topology_path.exists()


LANES: dict[str, LaneSpec] = {
    "jpl": LaneSpec(
        lane="jpl",
        label="JusticePlutus (Python)",
        languages=["python"],
        source_repo_id="jpl",
        source_root="/Users/wu/Documents/JusticePlutus",
        data_capability="PARTIAL",
        notes=[
            "DATA capability = PARTIAL: no resolved DATA wires in the frozen "
            "topology (joern dataflow + local bindings could not resolve "
            "producer/consumer chains upstream; see MODULE_INFER0_REPORT).",
            "STATE/CONTROL/TIME edge counts are 0 in the frozen topology; "
            "runtime evidence (OBSERVED_HAPPENS_BEFORE) does not exist for "
            "this lane and is not invented by the UI.",
        ],
    ),
    "fac_c": LaneSpec(
        lane="fac_c",
        label="FAC (C: sfac/scrm/stoken/spol)",
        languages=["c"],
        source_repo_id="fac",
        source_root="/Users/wu/Documents/dh/a3/fac",
        data_capability="PARTIAL",
        notes=[
            "C cross-procedural DATA = PARTIAL (verbatim from the "
            "MODULE-INFER0 lane note).",
            "Fortran semantic enrichment = PARTIAL where appropriate "
            "(lfortran_fac_dger.json exists upstream; no Fortran lane is "
            "part of the frozen topology set).",
            "Python-side UI completeness does NOT imply FAC DATA coverage — "
            "capability is per-lane and shown honestly.",
        ],
    ),
    "fixture_c": LaneSpec(
        lane="fixture_c",
        label="Fixture C (TOPO-ENGINE0 fixture_c)",
        languages=["c"],
        source_repo_id=None,
        source_root=None,
        data_capability="PARTIAL",
        notes=[
            "UI fixture lane (TOPO-ENGINE0 fixture_c output).  Source "
            "drill-down unavailable: the fixture repo is not part of the "
            "registered/indexed repository set.",
        ],
    ),
    "perf20": LaneSpec(
        lane="perf20",
        label="Perf 20 (synthetic UI fixture)",
        languages=["python"],
        source_repo_id=None,
        source_root=None,
        data_capability="MIXED",
        notes=[
            "SYNTHETIC lane: generated deterministically by "
            "analysis_tournament/gen_perf_lane.py.  NOT a repository "
            "analysis — exists so the UI's overlay/data-wire machinery can "
            "be exercised and measured (20-node performance gate).",
        ],
        synthetic=True,
    ),
    "perf100": LaneSpec(
        lane="perf100",
        label="Perf 100 (synthetic UI fixture)",
        languages=["python"],
        source_repo_id=None,
        source_root=None,
        data_capability="MIXED",
        notes=[
            "SYNTHETIC lane: generated deterministically by "
            "analysis_tournament/gen_perf_lane.py.  NOT a repository "
            "analysis — 100-node performance gate.",
        ],
        synthetic=True,
    ),
    "perf300": LaneSpec(
        lane="perf300",
        label="Perf 300 (synthetic UI fixture)",
        languages=["python"],
        source_repo_id=None,
        source_root=None,
        data_capability="MIXED",
        notes=[
            "SYNTHETIC lane: generated deterministically by "
            "analysis_tournament/gen_perf_lane.py.  NOT a repository "
            "analysis — 300-node performance gate.",
        ],
        synthetic=True,
    ),
}


def _lane_files(lane: LaneSpec) -> Path | None:
    return lane.topology_path if lane.available() else None


@lru_cache(maxsize=None)
def _load_json(path_str: str) -> dict | None:
    return json.loads(Path(path_str).read_text())


def _topology(spec: LaneSpec) -> dict:
    return _load_json(str(spec.topology_path))


def _candidates(spec: LaneSpec) -> dict | None:
    p = spec.candidates_path
    return _load_json(str(p)) if p.exists() else None


def _route_b(spec: LaneSpec) -> dict | None:
    p = spec.route_b_path
    if not p.exists():
        return None
    data = _load_json(str(p))
    if data.get("lane") != spec.lane:
        return None
    return data


def _route_c(spec: LaneSpec) -> dict | None:
    p = spec.route_c_path
    if not p.exists():
        return None
    data = _load_json(str(p))
    if data.get("lane") != spec.lane:
        return None
    return data


def lane_list() -> list[dict]:
    out = []
    for lane in sorted(LANES.values(), key=lambda s: (s.synthetic, s.lane)):
        if not lane.available():
            continue
        topo = _topology(lane)["topology"]
        out.append({
            "lane": lane.lane,
            "label": lane.label,
            "languages": lane.languages,
            "data_capability": lane.data_capability,
            "synthetic": lane.synthetic,
            "node_count": len(topo.get("nodes", [])),
            "edge_count": len(topo.get("edges", [])),
        })
    return out


# ---------------------------------------------------------------------------
# source path resolution (Monaco drill-down)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _index_roots(root: str) -> dict[str, str]:
    """filename -> first repo-relative path (deterministic sorted walk)."""
    rootp = Path(root)
    found: dict[str, str] = {}
    stack = [rootp]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _PRUNE_DIRS:
                    continue
                stack.append(entry)
            elif entry.is_file():
                rel = entry.relative_to(rootp).as_posix()
                found.setdefault(entry.name, rel)
    return found


def resolve_repo_path(source_root: str | None, hint: str | None) -> str | None:
    """Map a topology/witness file hint to a repo-relative path.

    Order: absolute under root -> direct relative hit -> unique suffix
    match (cached).  Returns None when unresolved (the UI then shows an
    honest 'source unavailable' state instead of fabricating a path).
    """
    if not source_root or not hint:
        return None
    root = Path(source_root)
    if hint.startswith("/"):
        p = Path(hint)
        try:
            rel = p.relative_to(root)
            if (root / rel).is_file():
                return rel.as_posix()
        except ValueError:
            pass
        hint = Path(hint).name
    direct = root / hint
    if direct.is_file():
        return hint.replace("\\", "/")
    rel = _index_roots(source_root).get(Path(hint).name)
    return rel


# ---------------------------------------------------------------------------
# bundle construction
# ---------------------------------------------------------------------------

def _node_repo_path(spec: LaneSpec, node: dict) -> str | None:
    if spec.synthetic:
        return None
    return resolve_repo_path(spec.source_root, node.get("file") or None)


def _suggestions(spec: LaneSpec) -> list[dict]:
    """MODULE-OPT0 Route B suggestions merged with MODULE-INFER0 candidates.

    Route B is the production route (MODULE-OPT0 decision); lanes without a
    Route B artifact (FAC) are served the MODULE-INFER0 candidate set with
    an explicit provenance note (never re-scored here).
    """
    mi = _candidates(spec)
    rb = _route_b(spec)
    infer_by_members: dict[frozenset, dict] = {}
    if mi:
        for c in mi.get("candidates", []):
            infer_by_members[frozenset(c.get("member_function_ids", []))] = c

    out: list[dict] = []
    if rb:
        for entry in rb.get("candidates", []):
            members = entry.get("members", [])
            base = infer_by_members.get(frozenset(members))
            out.append({
                "origin": "route_b",
                "candidate_id": (base or {}).get("candidate_id",
                                                 f"{spec.lane}-suggestion-{len(out)}"),
                "candidate_kind": (base or {}).get("candidate_kind",
                                                   "MODULE"),
                "members": members,
                "score": entry.get("score"),
                "score_components": entry.get("score_components"),
                "interface_diagnostics": entry.get("interface_diagnostics"),
                "soft_features": entry.get("soft_features"),
                "hard_features": entry.get("hard_features"),
                "why": entry.get("why", []),
                "legal": entry.get("legal", []),
                "classification": (base or {}).get("classification"),
                "confidence": (base or {}).get("confidence"),
                "witnesses": (base or {}).get("witnesses", []),
                "rejection_reasons": (base or {}).get("rejection_reasons", []),
                "boundary": (base or {}).get("boundary"),
                "truth_class": "SUGGESTED",
                "data_capability": (mi or {}).get(
                    "data_capability", spec.data_capability),
            })
        out.sort(key=lambda s: -(s.get("score") or 0.0))
        return out
    if mi:
        for c in mi.get("candidates", []):
            out.append({
                "origin": "module_infer",
                "candidate_id": c.get("candidate_id",
                                      f"{spec.lane}-suggestion-{len(out)}"),
                "candidate_kind": c.get("candidate_kind", "MODULE"),
                "members": c.get("member_function_ids", []),
                "score": (c.get("score_components", {})
                          .get("derived", {}).get("score")),
                "score_components": c.get("score_components"),
                "interface_diagnostics": None,
                "soft_features": None,
                "hard_features": c.get("features"),
                "why": c.get("why", []),
                "legal": [],
                "classification": c.get("classification"),
                "confidence": c.get("confidence"),
                "witnesses": c.get("witnesses", []),
                "rejection_reasons": c.get("rejection_reasons", []),
                "boundary": c.get("boundary"),
                "truth_class": c.get("truth_class", "SUGGESTED"),
                "data_capability": (mi or {}).get(
                    "data_capability", spec.data_capability),
            })
        out.sort(key=lambda s: -(s.get("score") or 0.0))
    return out


def build_lane_bundle(lane_id: str) -> dict:
    spec = LANES.get(lane_id)
    if spec is None or not spec.available():
        raise KeyError(lane_id)
    raw = _topology(spec)
    topo = raw["topology"]
    edges = topo["edges"]
    overlay_stats = {k: 0 for k in RELATION_KINDS}
    for e in edges:
        kind = e.get("kind", "CALL")
        if kind in overlay_stats:
            overlay_stats[kind] += 1

    nodes = []
    for n in topo.get("nodes", []):
        node = dict(n)
        node["repo_path"] = _node_repo_path(spec, n)
        node["identity_authority"] = "REFERENCE_UNBOUND"
        node["canonical_id_is_current"] = False
        nodes.append(node)

    declared: set[str] = set()
    for n in topo.get("nodes", []):
        file = n.get("file") or ""
        parts = [p for p in file.split("/") if p]
        if parts:
            declared.add(parts[0])

    mi = _candidates(spec)
    cross_cutting = list((mi or {}).get("cross_cutting", []))

    route_c = _route_c(spec)
    route_c_out = None
    if route_c:
        comms = route_c.get("communities") or []
        route_c_out = {
            "origin": "NetworKit PLM diagnostic challenger (MODULE-OPT0)",
            "production": False,
            "modularity": route_c.get("modularity"),
            "soft_config": route_c.get("soft_config"),
            "legal_count": sum(
                1 for c in comms if c.get("legal_status") == "LEGAL"),
            "communities": comms,
        }

    return {
        "lane": lane_id,
        "meta": {
            "label": spec.label,
            "identity_authority": "REFERENCE_UNBOUND",
            "identity_note": "Frozen artifact symbol IDs are not bound to CURRENT canonical Store identity",
            "languages": spec.languages,
            "data_capability": spec.data_capability,
            "synthetic": spec.synthetic,
            "capability_notes": list(spec.notes),
            "facts_total": raw.get("facts_total"),
            "symbols_total": raw.get("symbols_total"),
            "bound_symbols": raw.get("bound_symbols"),
            "unbound_symbols": raw.get("unbound_symbols"),
            "snapshot_id": (topo.get("snapshot_id")
                            or (nodes[0].get("snapshot_id") if nodes else
                                None)),
            "resources": topo.get("resources", []),
            "placeholders": topo.get("placeholders", []),
            "bound_count": topo.get("bound_count"),
            "overlay_stats": overlay_stats,
            "source_repo_id": spec.source_repo_id,
            "source_root": spec.source_root,
            "suggestion_origin": "route_b" if _route_b(spec) else
            "module_infer",
        },
        "nodes": nodes,
        "edges": edges,
        "declared_modules": sorted(declared),
        "cross_cutting": cross_cutting,
        "suggestions": _suggestions(spec),
        "route_c": route_c_out,
    }
