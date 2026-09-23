"""SOFTWARE-LVS1 normalized inputs + helper for code-side extraction.

Design side (FlowModel/Blocks/Ports/Nets/Composites) and code side
(canonical topology + signatures) are kept strictly separate — the engine
never touches code truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LvsDesign:
    snapshot_id: str | None = None
    blocks: list[dict] = field(default_factory=list)
    composites: list[dict] = field(default_factory=list)
    ports: list[dict] = field(default_factory=list)
    nets: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)   # control/timing claims

    def block(self, bid: str) -> dict | None:
        return next((b for b in self.blocks if b.get("id") == bid), None)

    def block_by_binding(self, cid: str) -> dict | None:
        return next((b for b in self.blocks
                     if b.get("binding") == cid), None)

    def ports_of(self, bid: str) -> list[dict]:
        return [p for p in self.ports if p.get("block_id") == bid]


@dataclass
class LvsCodeSide:
    snapshot_id: str | None = None
    baseline_id: str | None = None
    functions: dict[str, dict] = field(default_factory=dict)   # cid -> fn view
    baseline_functions: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    baseline_edges: list[dict] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    call_capability: str = "COMPLETE"     # lane-level CALL proof capability
    data_capability: str = "COMPLETE"     # lane-level DATA proof capability
    by_source: dict[str, list[dict]] = field(default_factory=dict)
    by_target: dict[str, list[dict]] = field(default_factory=dict)


def resolve_design(raw: dict) -> LvsDesign:
    return LvsDesign(
        snapshot_id=raw.get("snapshot_id"),
        blocks=raw.get("blocks", []),
        composites=raw.get("composites", []),
        ports=raw.get("ports", []),
        nets=raw.get("nets", []),
        claims=raw.get("claims", []),
    )


def resolve_code(raw: dict) -> LvsCodeSide:
    side = LvsCodeSide(
        snapshot_id=raw.get("snapshot_id"),
        baseline_id=raw.get("baseline_id"),
        functions=raw.get("functions", {}),
        baseline_functions=raw.get("baseline_functions", {}),
        edges=raw.get("edges", []),
        baseline_edges=raw.get("baseline_edges", []),
        resources=raw.get("resources", []),
        call_capability=raw.get("call_capability", "COMPLETE"),
        data_capability=raw.get("data_capability", "COMPLETE"),
    )
    for e in side.edges:
        side.by_source.setdefault(e.get("source", ""), []).append(e)
        side.by_target.setdefault(e.get("target", ""), []).append(e)
    return side


# ---------------------------------------------------------------------------
# code-side builder for real lanes (topology artifact + source signatures)
# ---------------------------------------------------------------------------

def build_code_side(lane_id: str, *, source_root: str | None,
                    with_signatures: bool = True) -> LvsCodeSide:
    """Frozen topology artifact + line-based signature extraction.

    Signatures come from the frozen source at the node's file — never from
    AnalysisFact mutation.  Parse failures yield an empty signature and the
    port comparators then answer UNKNOWN (L3).
    """
    from ..drc.run import lane_drc_input
    from .signatures import signature_of_name, signature_from_span

    inp, _dataflows, _facts_nodes = lane_drc_input(lane_id)
    topo = inp["topology"]
    nodes = {n["canonical_symbol_id"]: n for n in topo["nodes"]}
    side = LvsCodeSide(
        snapshot_id=(nodes[next(iter(nodes))]["snapshot_id"]
                     if nodes else None),
        edges=list(topo["edges"]),
        resources=sorted({e["target"] for e in topo["edges"]
                          if e.get("kind") == "RESOURCE"}),
    )
    root = Path(source_root) if source_root else None
    cache: dict[str, str] = {}
    for cid, n in nodes.items():
        file = n.get("file", "")
        language = n.get("language", "python") or "python"
        if not with_signatures or not root or not file:
            sig = {"params": [], "returns": True, "return_type": None,
                   "parse_failed": True}
        else:
            if file not in cache:
                full = root / _resolve_path(root, file)
                try:
                    cache[file] = full.read_text() if full.is_file() else ""
                except OSError:
                    cache[file] = ""
            sig = signature_of_name(cache[file], n["name"], language)
        side.functions[cid] = {
            "name": n["name"], "file": file, "language": language,
            "signature": sig, "span": None,
        }
    return side


def _resolve_path(root: Path, file: str) -> str:
    try:
        from ..topology_view.projector import resolve_repo_path
        return resolve_repo_path(str(root), file) or file
    except Exception:
        return file
