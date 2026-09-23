"""MODULE-INFER0: Hard Topology → Candidate Boundary inference.

Authority: TOPO-ENGINE0 (frozen).  Derivation order (spec §2):
hard dependencies → state/data ownership → precedence → lifecycle/trigger/
frequency → resource boundary → external interface boundary → legal
candidate regions.

Principles (frozen):
- NO directory/name-similarity/Louvain/Leiden/community detection.
- Everything is derived from Hard Topology edges + explicit soft metadata.
- All output is SUGGESTED — never canonical evidence / human architecture.
- candidate_kind FLOW / COMPOSITE / MODULE are separate lenses; membership
  may overlap but the three are never conflated.
- Cross-cutting hubs (Logger/Config/Database/Telemetry-like) are detected
  and marked; high degree alone never forces giant modules.
- SCC/cycles remain intact; SCC condensation is an analysis view only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

CC_FANIN = 25          # cross-cutting by degree
CC_NAMES = {"logger", "log", "config", "db", "database", "telemetry",
            "metrics", "timer", "cache", "memory"}

WOOD = 1.0             # non-frozen neutral weights (documented, not truth)
_W = {"internal_data": 1.0, "internal_state": 1.0, "internal_call": 0.5,
      "boundary_data": -1.0, "boundary_state": -1.5, "boundary_call": -0.7,
      "boundary_resource": -0.8, "uncertainty": -1.2}


@dataclass
class Candidate:
    candidate_id: str
    candidate_kind: str          # FLOW | COMPOSITE | MODULE
    member_function_ids: list[str]
    boundary: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    classification: dict = field(default_factory=dict)
    score_components: dict = field(default_factory=dict)
    truth_class: str = "SUGGESTED"
    confidence: float = 0.0
    witnesses: list[dict] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "member_function_ids": list(self.member_function_ids),
            "boundary": self.boundary,
            "features": self.features,
            "classification": self.classification,
            "score_components": self.score_components,
            "truth_class": self.truth_class,
            "confidence": round(self.confidence, 3),
            "witnesses": self.witnesses[:5],
            "rejection_reasons": self.rejection_reasons,
            "why": self.why,
        }


class TopologyArtifact:
    """Renderer-neutral view of the frozen topology (TOPO-ENGINE0 output)."""

    def __init__(self, data: dict):
        self.nodes = {n["canonical_symbol_id"]: n for n in data["nodes"]}
        self.edges = data["edges"]
        self.by_source = defaultdict(list)
        self.by_target = defaultdict(list)
        for e in self.edges:
            self.by_source[e["source"]].append(e)
            self.by_target[e["target"]].append(e)

    def declared_module(self, nid: str) -> Optional[str]:
        n = self.nodes.get(nid)
        if not n:
            return None
        return n.get("file", "").split("/")[0]


class CandidateInferer:
    def __init__(self, artifact: TopologyArtifact,
                 data_capability: str = "COMPLETE"):
        self.topo = artifact
        self.data_capability = data_capability
        self.cross_cutting: set[str] = set()
        self._detect_cross_cutting()

    # -- cross-cutting ---------------------------------------------------------
    def _detect_cross_cutting(self) -> None:
        fanin = defaultdict(int)
        for e in self.topo.edges:
            if e["kind"] in ("CALL", "DATA", "STATE") \
                    and e["target"] in self.topo.nodes:
                fanin[e["target"]] += 1
        for nid, n in self.topo.nodes.items():
            name = n["name"].lower()
            if fanin[nid] >= CC_FANIN:
                self.cross_cutting.add(nid)
            elif any(h in name for h in CC_NAMES):
                self.cross_cutting.add(nid)

    # -- region seeds ----------------------------------------------------------
    def data_chains(self) -> list[list[str]]:
        """producer→transform→consumer chains via DATA edges (FLOW seeds)."""
        data_by_src = defaultdict(list)
        for e in self.topo.edges:
            if e["kind"] == "DATA" and e.get("data"):
                data_by_src[e["source"]].append(e)
        chains: list[list[str]] = []
        visited: set[tuple] = set()
        for start in self.topo.nodes:
            # start chains at nodes with DATA out-edges and few in-edges
            stack = [(start, [start])]
            while stack:
                cur, path = stack.pop()
                if len(path) > 5:
                    continue
                outs = data_by_src.get(cur, [])
                if not outs:
                    if len(path) >= 2:
                        key = tuple(path)
                        if key not in visited:
                            visited.add(key)
                            chains.append(list(path))
                    continue
                for e in outs:
                    tgt = e["target"]
                    if tgt in path:
                        continue
                    # only continue on same data token (continuity)
                    stack.append((tgt, path + [tgt]))
        chains.sort(key=len, reverse=True)
        return chains[:80]

    def state_regions(self) -> list[list[str]]:
        """Connected components under STATE edges (ownership regions)."""
        parent: dict[str, str] = {}
        for e in self.topo.edges:
            if e["kind"] != "STATE":
                continue
            for nid in (e["source"], e["target"]):
                if nid not in self.topo.nodes:
                    continue
                parent.setdefault(nid, nid)
        # union
        def find(x):
            while parent.get(x) != x:
                parent[x] = parent.get(parent[x], x)
                x = parent[x]
            return x
        for e in self.topo.edges:
            if e["kind"] != "STATE":
                continue
            s, t = e["source"], e["target"]
            if s not in parent or t not in parent:
                continue
            rs, rt = find(s), find(t)
            if rs != rt:
                parent[rs] = rt
        groups = defaultdict(list)
        for nid in list(parent):
            groups[find(nid)].append(nid)
        return [g for g in groups.values() if len(g) >= 2][:40]

    # -- core candidates -------------------------------------------------------
    def _boundary_of(self, members: set[str]) -> dict:
        din: dict[str, int] = defaultdict(int)
        dout: dict[str, int] = defaultdict(int)
        sin: dict[str, int] = defaultdict(int)
        cin: dict[str, int] = defaultdict(int)
        cout: dict[str, int] = defaultdict(int)
        resid = defaultdict(set)
        for e in self.topo.edges:
            src_in, tgt_in = e["source"] in members, e["target"] in members
            if src_in and tgt_in:
                continue
            if e["kind"] == "DATA":
                tok = e.get("data") or e["target"]
                if tgt_in and not src_in:
                    din[tok] += 1
                elif src_in and not tgt_in:
                    dout[tok] += 1
            elif e["kind"] == "STATE":
                tok = e.get("data") or e["target"]
                if src_in or tgt_in:
                    sin[tok] += 1
            elif e["kind"] == "CALL":
                if tgt_in and not src_in:
                    cin[e["source"]] += 1
                elif src_in and not tgt_in:
                    cout[e["target"]] += 1
            elif e["kind"] == "RESOURCE":
                if src_in and e["target"] in ("FileSystem", "Database",
                                              "GPU", "Network"):
                    resid[e["target"]].add(e["source"])
        def top(d, n=12):
            return sorted(d.items(), key=lambda kv: -kv[1])[:n]
        return {
            "data_in": [{"data": k, "count": v} for k, v in top(din)],
            "data_out": [{"data": k, "count": v} for k, v in top(dout)],
            "state_inout": [{"data": k, "count": v} for k, v in top(sin)],
            "resource_ports": [{"resource": k,
                                "users": sorted(v)[:6]}
                               for k, v in sorted(resid.items())],
            "calls_in": [{"from": k, "count": v} for k, v in top(cin, 8)],
            "calls_out": [{"to": k, "count": v} for k, v in top(cout, 8)],
        }

    def _features(self, members: set[str], boundary: dict) -> dict:
        internal = defaultdict(int)
        crossing = {"DATA": 0, "STATE": 0, "CALL": 0, "RESOURCE": 0}
        resources = set()
        uncertainty = 0
        for e in self.topo.edges:
            s, t = e["source"] in members, e["target"] in members
            if s and t:
                internal[e["kind"]] += 1
                if e.get("target_resolution") in ("UNKNOWN", "CANDIDATE_SET") \
                        or e.get("coverage") in ("PARTIAL", "UNKNOWN"):
                    uncertainty += 1
                if e["kind"] == "RESOURCE":
                    resources.add(e["target"])
            elif s or t:
                crossing[e["kind"]] += 1
                if e.get("target_resolution") == "UNKNOWN":
                    uncertainty += 1
        modules = {self.topo.declared_module(nid) for nid in members}
        modules.discard(None)
        return {
            "internal_relation_counts": dict(internal),
            "boundary_relation_counts": crossing,
            "types": sorted({self.topo.nodes[nid]["name"]
                             for nid in members})[:0],
            "resources": sorted(resources),
            "uncertainty": uncertainty,
            "declared_modules": sorted(modules),
        }

    def _reject(self, members: set[str], boundary: dict,
                features: dict) -> list[str]:
        reasons = []
        # R1 hidden required input: none if any data_in (exposed)
        if boundary["data_in"] or not features["internal_relation_counts"].get(
                "DATA"):
            pass
        # R4 resource concealment: resource users must appear on resource_ports
        res_used = features["resources"]
        res_exposed = {r["resource"] for r in boundary["resource_ports"]}
        for r in res_used:
            if r not in res_exposed:
                reasons.append(f"R4_resource_concealment:{r}")
        # R3 state ownership violation: heavy state crossing
        if features["boundary_relation_counts"]["STATE"] >= 3:
            reasons.append("R3_state_crossing_heavy")
        # R5 false ordering: boundary STATE edges imply ordering — no claim
        # R6 dynamic uncertainty: heavy unknown
        if features["uncertainty"] >= 5:
            reasons.append("R6_uncertainty_high")
        return reasons

    def call_regions(self) -> list[list[str]]:
        """CALL-derived legal regions: connected components of the CALL
        subgraph restricted to nodes INSIDE the same declared module
        (hard dependencies first; module coherence as legality filter —
        never auto-changing membership)."""
        parent: dict[str, str] = {}

        def find(x):
            while parent.get(x) != x:
                parent[x] = parent.get(parent[x], x)
                x = parent[x]
            return x

        def union(a, b):
            parent.setdefault(a, a); parent.setdefault(b, b)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for e in self.topo.edges:
            if e["kind"] != "CALL":
                continue
            s, t = e["source"], e["target"]
            if s not in self.topo.nodes or t not in self.topo.nodes:
                continue
            if self.topo.declared_module(s) != \
                    self.topo.declared_module(t):
                continue  # cross-module CALL is boundary, not member glue
            union(s, t)
        groups = defaultdict(list)
        for nid in list(parent):
            groups[find(nid)].append(nid)
        return [g for g in groups.values() if len(g) >= 3][:60]

    def _classify(self, members: set[str],
                  declared: set[str]) -> dict:
        """Compare with declared module membership (descriptive only)."""
        if len(declared) == 1:
            dmod = next(iter(declared))
            nfiles = {self.topo.nodes[nid]["file"]
                      for nid in members if nid in self.topo.nodes}
            # all members same declared module → ALIGNED or SUBSET
            inside = all(self.topo.declared_module(nid) == dmod
                         for nid in members if nid in self.topo.nodes)
            return {"declared_module_relation":
                    "ALIGNED" if inside else "CROSS_CUTTING",
                    "declared_module": dmod,
                    "description": "all members belong to one declared "
                                   "package" if inside else
                                   "crosses declared module boundaries"}
        if len(declared) > 1:
            return {"declared_module_relation": "CONFLICTING",
                    "declared_module": sorted(declared),
                    "description": "candidate spans multiple declared "
                                   "packages — descriptive conflict"}
        return {"declared_module_relation": "UNRELATED",
                "declared_module": None,
                "description": "no declared module membership found"}

    def _score(self, features: dict, boundary: dict) -> dict:
        raw = {
            "internal_data": features["internal_relation_counts"].get("DATA", 0),
            "internal_state": features["internal_relation_counts"].get("STATE", 0),
            "internal_call": features["internal_relation_counts"].get("CALL", 0),
            "boundary_data": features["boundary_relation_counts"]["DATA"],
            "boundary_state": features["boundary_relation_counts"]["STATE"],
            "boundary_call": features["boundary_relation_counts"]["CALL"],
            "boundary_resource": features["boundary_relation_counts"]["RESOURCE"],
            "uncertainty": features["uncertainty"],
        }
        cohesion = (_W["internal_data"] * raw["internal_data"]
                    + _W["internal_state"] * raw["internal_state"]
                    + _W["internal_call"] * raw["internal_call"])
        cost = (_W["boundary_data"] * raw["boundary_data"]
                + _W["boundary_state"] * raw["boundary_state"]
                + _W["boundary_call"] * raw["boundary_call"]
                + _W["boundary_resource"] * raw["boundary_resource"])
        penalty = _W["uncertainty"] * raw["uncertainty"]
        return {"raw_feature_vector": raw,
                "derived": {
                    "cohesion": round(cohesion, 3),
                    "boundary_cost": round(cost, 3),
                    "uncertainty_penalty": round(penalty, 3),
                    "score": round(cohesion + cost + penalty, 3),
                    "weights_note": "neutral weights, NOT frozen truth; "
                                    "components recorded separately"},
                "data_capability": self.data_capability}

    # -- build -----------------------------------------------------------------
    def build_candidates(self) -> list[Candidate]:
        cands: list[Candidate] = []
        idx = 0
        # FLOW candidates from data chains
        for chain in self.data_chains():
            members = {n for n in chain if n in self.topo.nodes
                       and n not in self.cross_cutting}
            if len(members) < 2:
                continue
            boundary = self._boundary_of(members)
            features = self._features(members, boundary)
            reasons = self._reject(members, boundary, features)
            decl = self._classify(members, set(features["declared_modules"]))
            score = self._score(features, boundary)
            conf = 0.75 - 0.05 * len(reasons)
            if self.data_capability != "COMPLETE":
                conf = min(conf, 0.4)
            idx += 1
            cands.append(Candidate(
                candidate_id=f"jpl-flow-{idx:03d}",
                candidate_kind="FLOW",
                member_function_ids=sorted(members),
                boundary=boundary, features=features,
                classification=decl,
                score_components=score,
                confidence=conf,
                witnesses=[{"chain": list(chain)}],
                rejection_reasons=reasons,
                why=[f"data_chain:{'->'.join(
                     self.topo.nodes[n]['name'] for n in chain if n in
                     self.topo.nodes)[:90]}",
                     f"internal: {features['internal_relation_counts']}",
                     f"boundary_in/out: {len(boundary['data_in'])}/"
                     f"{len(boundary['data_out'])}",
                     f"uncertainty: {features['uncertainty']}"]))
        # MODULE / COMPOSITE candidates from state ownership regions
        for region in self.state_regions():
            members = {n for n in region if n in self.topo.nodes
                       and n not in self.cross_cutting}
            if len(members) < 2:
                continue
            boundary = self._boundary_of(members)
            features = self._features(members, boundary)
            reasons = self._reject(members, boundary, features)
            decl = self._classify(members, set(features["declared_modules"]))
            score = self._score(features, boundary)
            conf = 0.6 - 0.05 * len(reasons)
            if features["boundary_relation_counts"]["STATE"] > 2:
                conf = min(conf, 0.35)
            idx += 1
            cands.append(Candidate(
                candidate_id=f"jpl-module-{idx:03d}",
                candidate_kind="MODULE",
                member_function_ids=sorted(members),
                boundary=boundary, features=features,
                classification=decl,
                score_components=score,
                confidence=conf,
                witnesses=[{"state_region": sorted(region)}],
                rejection_reasons=reasons,
                why=[f"state_ownership({len(members)}): shared "
                     f"data={boundary['state_inout'][:3]}",
                     f"boundary_state: {features['boundary_relation_counts']['STATE']}",
                     f"declared: {decl.get('declared_module')}"]))
        # CALL-region seeds (legal candidate regions from hard CALL deps)
        for region in self.call_regions():
            members = {n for n in region if n not in self.cross_cutting}
            if len(members) < 3:
                continue
            boundary = self._boundary_of(members)
            features = self._features(members, boundary)
            reasons = self._reject(members, boundary, features)
            decl = self._classify(members, set(features["declared_modules"]))
            score = self._score(features, boundary)
            conf = 0.55 - 0.04 * len(reasons)
            idx += 1
            cands.append(Candidate(
                candidate_id=f"jpl-callreg-{idx:03d}",
                candidate_kind="MODULE",
                member_function_ids=sorted(members),
                boundary=boundary, features=features,
                classification=decl,
                score_components=score, confidence=conf,
                witnesses=[{"call_region": sorted(region)[:10]}],
                rejection_reasons=reasons,
                why=[f"intra-module CALL component({len(members)}): "
                     f"internal_call={features['internal_relation_counts'].get('CALL', 0)}",
                     f"boundary_calls: {features['boundary_relation_counts']['CALL']}",
                     f"declared: {decl.get('declared_module')}"]))
        # COMPOSITE: chains that ALSO have narrow external ports and
        # same declared module (encapsulation lens over the flow)
        for chain in self.data_chains():
            members = {n for n in chain if n in self.topo.nodes
                       and n not in self.cross_cutting}
            if len(members) < 3:
                continue
            boundary = self._boundary_of(members)
            features = self._features(members, boundary)
            decl = self._classify(members, set(features["declared_modules"]))
            if decl.get("declared_module_relation") not in (
                    "ALIGNED", "SUBSET"):
                continue  # composite lens requires module coherence
            if len(boundary["data_out"]) > 3:
                continue
            score = self._score(features, boundary)
            reasons = self._reject(members, boundary, features)
            idx += 1
            cands.append(Candidate(
                candidate_id=f"jpl-composite-{idx:03d}",
                candidate_kind="COMPOSITE",
                member_function_ids=sorted(members),
                boundary=boundary, features=features,
                classification=decl,
                score_components=score,
                confidence=round(min(0.7 - 0.05 * len(reasons),
                                     (score["derived"]["score"] / 8)
                                     if score["derived"]["score"] > 0 else 0.3),
                                 3),
                witnesses=[{"chain": list(chain)}],
                rejection_reasons=reasons,
                why=[f"chain:{'->'.join(self.topo.nodes[n]['name'] for n in
                                        chain if n in self.topo.nodes)[:90]}",
                     f"external_data_ports: {len(boundary['data_in'])}/"
                     f"{len(boundary['data_out'])}",
                     f"declared: {decl.get('declared_module')}"]))
        # sort accepted first
        cands.sort(key=lambda c: (-len(c.rejection_reasons),
                                  -c.score_components["derived"]["score"]))
        return cands


def load_and_infer(path: str, data_capability: str = "COMPLETE") -> dict:
    data = json.load(open(path))
    if "topology" in data:          # TOPO-ENGINE0 artifact envelope
        data = data["topology"]
    topo = TopologyArtifact(data)
    inferer = CandidateInferer(topo, data_capability=data_capability)
    cands = inferer.build_candidates()
    defs = {
        "cross_cutting": sorted(inferer.cross_cutting),
        "candidates": [c.to_dict() for c in cands],
        "data_capability": data_capability,
    }
    return defs
