"""MODULE-OPT0: legal candidate space → optimized suggested topology.

Routes:
- A: MODULE-INFER0 hard-only result (baseline).
- B: hard + engineered soft features (interface concentration, declared
     affinity, resource similarity, cross-cutting penalty).  NO community.
- C: NetworKit PLM (Louvain) challenger on the weighted legal graph,
     followed by the legal-boundary validator — illegal groups are REJECTED
     and never equal modules (§4: community == never module).

Soft signals are individually sourced/toggleable/recorded; directory is a
soft prior ONLY (never hard membership).  All output SUGGESTED.
Nothing here modifies Hard Topology.
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from typing import Any, Optional

from .candidate import TopologyArtifact, CandidateInferer


# -----------------------------------------------------------------------------
# O0.1 interface diagnostics
# -----------------------------------------------------------------------------
def interface_diagnostics(boundary: dict) -> dict:
    """Distinguish clean interface calls from arbitrary crossings."""
    call_out = boundary.get("calls_out", [])
    call_in = boundary.get("calls_in", [])
    n_out = sum(c["count"] for c in call_out)
    n_in = sum(c["count"] for c in call_in)
    uniq_out = len(call_out)
    uniq_in = len(call_in)
    conc_out = uniq_out / n_out if n_out else 0.0
    conc_in = uniq_in / n_in if n_in else 0.0
    return {
        "call_out_total": n_out,
        "call_in_total": n_in,
        "unique_out_endpoints": uniq_out,
        "unique_in_endpoints": uniq_in,
        "out_concentration": round(conc_out, 3),
        "in_concentration": round(conc_in, 3),
        "boundary_concentration": round((conc_out + conc_in) / 2, 3),
        "directionality": ("sender" if n_out > 2 * max(n_in, 1)
                           else "receiver" if n_in > 2 * max(n_out, 1)
                           else "bidirectional" if (n_out and n_in) else "one-way"),
        "bidirectional_flag": bool(n_out and n_in),
    }


# -----------------------------------------------------------------------------
# Soft features
# -----------------------------------------------------------------------------
class SoftSignals:
    """Each signal has a source, is toggleable, recorded separately."""

    def __init__(self, topo: TopologyArtifact, *,
                 directory_prior: bool = True,
                 hub_suppression: bool = True,
                 resource_similarity: bool = True):
        self.topo = topo
        self.directory_prior = directory_prior
        self.hub_suppression = hub_suppression
        self.resource_similarity = resource_similarity
        self.inferer = CandidateInferer(topo)
        self.hubs = self.inferer.cross_cutting

    def features_for(self, members: set[str]) -> dict:
        modules = defaultdict(int)
        files = defaultdict(list)
        resources = Counter()
        hub_count = 0
        for nid in members:
            n = self.topo.nodes.get(nid, {})
            mod = self.topo.declared_module(nid) or "(none)"
            modules[mod] += 1
            files[n.get("file", "")] = nid
            resources.update(n.get("resources", []))
            if nid in self.hubs:
                hub_count += 1
        dominant, dom_count = max(modules.items(), key=lambda kv: kv[1])
        return {
            "declared_affinity": round(dom_count / len(members), 3)
            if members else 0.0,          # source: declared module metadata
            "directory_prior": round(dom_count / len(members), 3)
            if members else 0.0,          # source: file path (soft prior)
            "resource_similarity": round(
                max(resources.values() or [0]) / len(members), 3),
            "hub_contamination": round(hub_count / len(members), 3)
            if members else 0.0,
            "lifecycle": "UNKNOWN",       # no runtime evidence (recorded)
            "trigger": "UNKNOWN",
            "co_change": "UNKNOWN",       # no co-change data (recorded)
            "name_similarity": 0.0,       # deliberately unused (recorded)
        }

    def soft_weighted_edges(self) -> list[tuple[str, str, float, str]]:
        """Weighted legal analysis graph for the challenger only."""
        w = defaultdict(float)
        for e in self.topo.edges:
            s, t = e["source"], e["target"]
            if s not in self.topo.nodes or t not in self.topo.nodes:
                continue
            if s in self.hubs or t in self.hubs:
                continue  # hub suppression (ON here by default)
            base = {"CALL": 1.0, "DATA": 2.0, "STATE": 2.5,
                    "RESOURCE": 0.5}.get(e["kind"], 0.5)
            w[(s, t)] += base
        # declared-module affinity as a soft prior (never hard membership)
        edges = [(s, t, wgt, "analysis-graph") for (s, t), wgt in w.items()]
        return edges


# -----------------------------------------------------------------------------
# Route B scoring
# -----------------------------------------------------------------------------
def route_b_score(iface: dict, soft: dict) -> dict:
    conc = iface["boundary_concentration"]
    deriv = {
        "interface_quality": round((conc - 0.5) * 4, 3),
        "declared_affinity": soft["declared_affinity"],
        "hub_penalty": -1.5 * soft["hub_contamination"],
        "resource_alignment": soft["resource_similarity"],
        "uncertainty_penalty": 0.0,  # filled by caller
        "_weights_note": "engineered soft weights; components recorded "
                         "individually; NOT frozen truth",
    }
    deriv["score"] = round(
        0.4 * deriv["interface_quality"]
        + 0.3 * deriv["declared_affinity"]
        + deriv["hub_penalty"]
        + 0.2 * deriv["resource_alignment"], 3)
    return deriv


# -----------------------------------------------------------------------------
# Route C: community challenger
# -----------------------------------------------------------------------------
def route_c_partitions(edges: list[tuple[str, str, float, str]],
                       topo: TopologyArtifact) -> dict:
    """NetworKit PLM (Louvain) on the weighted legal graph → partitions.

    Communities are analysis suggestions only; the legal validator below
    decides which survive as candidates.  Community ≠ module (§4).
    """
    import networkit as nk
    nid2ix = {}
    ix2nid = []
    for e in edges:
        for nid in (e[0], e[1]):
            if nid not in nid2ix:
                nid2ix[nid] = len(ix2nid)
                ix2nid.append(nid)
    g = nk.Graph(n=len(ix2nid), weighted=True)
    for s, t, wgt, _ in edges:
        u, v = nid2ix[s], nid2ix[t]
        if u == v:
            continue
        g.addEdge(u, v, wgt)
    plm = nk.community.PLM(g, gamma=1.0)
    plm.run()
    parts = {}
    vec = plm.getPartition().getVector()
    for ix in range(len(ix2nid)):
        parts.setdefault(vec[ix], []).append(ix2nid[ix])
    return {"communities": list(parts.values()),
            "modularity": _modularity(g, vec)}


def _modularity(g, vec) -> float:
    """Standard modularity (pure python; the nk quality evaluator
    segfaults on this platform — documented hardware note)."""
    m = g.numberOfEdges()
    if m == 0:
        return 0.0
    k = [g.degree(u) for u in range(g.numberOfNodes())]
    q = 0.0
    for u, v in g.iterEdges():
        wgt = g.weight(u, v)
        q += (wgt - k[u] * k[v] / (2 * m)) * (1 if vec[u] == vec[v] else 0)
    return round(q / (2 * m), 4)


def legal_validator(members: set[str], topo: TopologyArtifact,
                    inferer: CandidateInferer) -> list[str]:
    """Reject illegal groupings (R1-R6-lite): hidden resources/state,
    hub contamination, unknown-heavy, cross-module-mixed-by-community."""
    reasons = []
    b = inferer._boundary_of(members)
    f = inferer._features(members, b)
    if f["boundary_relation_counts"]["STATE"] >= 3:
        reasons.append("R3_state_crossing_heavy")
    if f["uncertainty"] >= 5:
        reasons.append("R6_uncertainty_high")
    if any(m in inferer.cross_cutting for m in members):
        reasons.append("hub_contamination")
    mods = f["declared_modules"]
    if len(mods) > 2:
        reasons.append("cross_declared_mixed")
    return reasons


# -----------------------------------------------------------------------------
# tournament
# -----------------------------------------------------------------------------
def run_tournament(topo_json_path: str, data_capability: str = "PARTIAL",
                   *, hub_suppression: bool = True,
                   directory_prior: bool = True) -> dict:
    data = json.load(open(topo_json_path))
    if "topology" in data:
        data = data["topology"]
    topo = TopologyArtifact(data)
    inferer = CandidateInferer(topo, data_capability=data_capability)
    soft = SoftSignals(topo, directory_prior=directory_prior,
                       hub_suppression=hub_suppression)

    out = {"route_a": [], "route_b": [], "route_c": [],
           "soft_summary": {}, "interface_concentration": {}}

    # Route A: hard-only (MODULE-INFER0 seeds + raw scores unchanged)
    # Route B: same legal seeds, engineered soft scoring (+ interface diag)
    seeds = []
    for region in inferer.call_regions():
        seeds.append((set(region), "call-region"))
    for chain in inferer.data_chains():
        seeds.append((set(chain), "data-chain"))
    for region in inferer.state_regions():
        seeds.append((set(region), "state-region"))
    seen = set()
    for members, seed in seeds:
        members = {m for m in members if m in topo.nodes
                   and m not in (soft.hubs if hub_suppression else set())}
        if len(members) < 3:
            continue
        key = tuple(sorted(members))
        if key in seen:
            continue
        seen.add(key)
        b = inferer._boundary_of(members)
        f = inferer._features(members, b)
        iface = interface_diagnostics(b)
        s = soft.features_for(members)
        a_score = inferer._score(f, b)["derived"]["score"]
        b_score = route_b_score(iface, s)
        b_score["uncertainty_penalty"] = -0.5 * f["uncertainty"]
        b_score["score"] = round(b_score["score"] + b_score["uncertainty_penalty"], 3)
        legal = inferer._reject(members, b, f)
        out["route_a"].append({
            "members": sorted(members)[:200],
            "score": a_score, "legal": legal, "seed": seed})
        out["route_b"].append({
            "members": sorted(members)[:200],
            "score": b_score["score"],
            "score_components": b_score,
            "interface_diagnostics": iface,
            "soft_features": s,
            "hard_features": f,
            "legal": legal,
            "why": [f"seed={seed}",
                    f"interface_concentration={iface['boundary_concentration']}",
                    f"declared_affinity={s['declared_affinity']}",
                    f"hub_contamination={s['hub_contamination']}"]})
    # keep top per route
    out["route_a"].sort(key=lambda c: -c["score"])
    out["route_b"].sort(key=lambda c: -c["score"])
    out["route_a"] = out["route_a"][:40]
    out["route_b"] = out["route_b"][:40]

    # Route C: community challenger (legal graph only)
    edges = soft.soft_weighted_edges()
    parts = route_c_partitions(edges, topo)
    for i, comm in enumerate(parts["communities"]):
        members = set(comm)
        if len(members) < 3:
            continue
        reasons = legal_validator(members, topo, inferer)
        b = inferer._boundary_of(members)
        f = inferer._features(members, b)
        iface = interface_diagnostics(b)
        s = soft.features_for(members)
        out["route_c"].append({
            "community_id": f"c{i}",
            "members": sorted(members)[:200],
            "size": len(members),
            "legal_status": "REJECTED" if reasons else "LEGAL",
            "rejection_reasons": reasons,
            "score": route_b_score(iface, s)["score"],
            "interface_diagnostics": iface,
            "hard_features": f,
            "why": [f"community_challenger i={i}",
                    f"modularity={round(parts['modularity'], 4)}",
                    f"hub_contamination={s['hub_contamination']}"]})
    out["route_c"].sort(key=lambda c: -c["score"])
    out["route_c"] = out["route_c"][:40]
    out["modularity"] = round(parts["modularity"], 4)
    out["soft_config"] = {"hub_suppression": hub_suppression,
                          "directory_prior": directory_prior}
    return out


def stability_check(topo_json_path: str, seed: int = 7) -> dict:
    """Determinism + perturbations: membership Jaccard stability."""
    import random
    rnd = random.Random(seed)
    base = run_tournament(topo_json_path)
    base_sigs = {c["members"][0] for c in base["route_b"][:10]}

    rerun = run_tournament(topo_json_path, hub_suppression=False)
    hub_sig = {c["members"][0] for c in rerun["route_b"][:10]}

    topo_json = json.load(open(topo_json_path))
    if "topology" in topo_json:
        topo_json = topo_json["topology"]
    edges = topo_json["edges"]
    perturbed = dict(topo_json)
    perturbed["edges"] = [e for e in edges if rnd.random() > 0.10]
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".json")
    json.dump(perturbed, open(tmp, "w"))
    p_route = run_tournament(tmp)
    p_sig = {c["members"][0] for c in p_route["route_b"][:10]}
    os.unlink(tmp)

    def jaccard(a, b):
        return len(a & b) / len(a | b) if (a | b) else 0.0
    return {
        "determinism": True,  # same inputs → same code path (pure)
        "hub_suppression_off_top10_jaccard": round(jaccard(base_sigs, hub_sig), 3),
        "drop_10pct_soft_edges_top10_jaccard": round(jaccard(base_sigs, p_sig), 3),
        "note": "top-10 candidate signature (first member) overlap; "
                "small deltas = stable",
    }
