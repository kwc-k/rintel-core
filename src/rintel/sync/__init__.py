"""CROSS-PROJECTION-SYNC0: source change -> incremental EvidenceDelta ->
selective invalidation/reprojection -> atomic revision publish.

Single truth chain (never edited here, only read):
    Source -> Canonical Evidence -> Derived Projections
This module derives projections (Data Interface, Module Boundary, Flow
Interface, Semantic Flow, LVS annotations, Frozen-topology policy) from the
evidence files of the tracked parse set; it NEVER mutates the Evidence plane,
the frozen FLOW-INFER0 regions, or the frozen TOPO-ENGINE0 graph directly.

Change units (delta vocabulary):
    FILE_ADDED / FILE_REMOVED / FILE_CHANGED
    SYMBOL_ADDED / SYMBOL_REMOVED / SYMBOL_CHANGED
    SIGNATURE_CHANGED / CALL_CHANGED / CONTROL_CHANGED / DATA_INTERFACE_CHANGED

Invariants:
    - canonical identity stable across content changes (id = function:NAME;
      rename = SYMBOL_REMOVED + SYMBOL_ADDED, never a silent merge)  (XS3)
    - comment-only edits produce an empty evidence delta (X1)
    - publish is commit-record atomic: every artifact is staged (staging/)
      then swapped BEFORE state.json is written; on failure the previous
      revision stays active and SYNC_FAILED is recorded (XS5/XS7)
    - regenerate-and-compare: a regenerated projection whose content is
      byte-identical is reported UNCHANGED (no phantom revisions)
    - ACCEPTED/REJECTED human semantic stages are never auto-rewritten:
      regeneration replaces only the projector output; the human overlay is
      reapplied and the stage is marked STALE_NEEDS_REVIEW (XS6)
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from rintel import data_interface as _di
from rintel import flow_semantic as _fs
from rintel import kernel as _krn
from rintel.data_interface import (C_SOURCE_SPECS, FORT_SOURCE_RELS,
                                   parse_c_function, parse_fortran_file)

REPO = Path(__file__).resolve().parents[3]
SYNC_ROOT = REPO / "analysis_tournament" / "cross_projection_sync0"
DI_OUT = REPO / "analysis_tournament" / "data_interface"
SEM_OUT = REPO / "analysis_tournament" / "flow_semantic0"
LVS_OUT = REPO / "analysis_tournament" / "lvs" / "fac_results.json"
FIN = REPO / "analysis_tournament" / "flow_infer"
KERNEL_OUT = REPO / "analysis_tournament" / "semantic_substrate1"
FAC_ROOT_DEFAULT = Path("/Users/wu/Documents/dh/a3/fac/fac")

BASE_REVISION = "r0"

# ---------------------------------------------------------------------------
# parse set + hashing
# ---------------------------------------------------------------------------

PARSE_SET: list[str] = list(FORT_SOURCE_RELS) + [rel for rel, _ in C_SOURCE_SPECS] \
    + [r for r in _krn.faclib_rels(FAC_ROOT_DEFAULT) if r.endswith(".c")]
C_NAMES: dict[str, list[str]] = dict(C_SOURCE_SPECS)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _fortran_body_hash(block: str) -> str:
    lines = [l for l in block.splitlines() if not re.match(r"^\s*[*cC!]", l)]
    return digest("\n".join(lines))


def _ports_key(ports) -> str:
    return "\x02".join(
        f"{p.name}|{p.direction}|{p.dtype}|{p.rank}|{p.shape_status}|"
        f"{','.join(p.shape or [])}" for p in ports)


# ---------------------------------------------------------------------------
# entity extraction (per file of the parse set)
# ---------------------------------------------------------------------------

def extract_file_entities(root: Path, rel: str) -> list[dict]:
    """one file -> entity records (canonical evidence at function granularity)."""
    path = root / rel
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    entities: list[dict] = []
    if rel in FORT_SOURCE_RELS:
        for meta, ports in parse_fortran_file(path):
            name = meta["name"]
            start, end = meta.get("line") or 0, meta.get("end_line") or start
            block = "\n".join(lines[start - 1:end]) if end >= start else ""
            calls = []
            # balanced-paren arg clause (same rule as the bindings path)
            for cm in _di.F_CALL_HEAD.finditer(block):
                calls.append({"callee": cm.group(1),
                              "expr": _di._call_args(block, cm.end()),
                              "line": start + block[:cm.start()].count("\n")})
            entities.append(_entity(meta, ports, block, calls))
        return entities
    if rel in C_NAMES:
        names = C_NAMES[rel]
    elif rel.startswith("faclib/") and rel.endswith(".c"):
        names = _di._all_c_fn_names(text)     # faclib coverage: all functions
    else:
        return entities
    for n in names:
        r = parse_c_function(path, n)
        if not r:
            continue
        meta, ports = r
        m = re.search(r"(?m)^[\w\s\*]*\b" + n + r"\s*\(([^)]*)\)\s*\{", text)
        if not m:
            continue
        body = _di._c_body(text, m.end())
        entities.append(_entity(meta, ports, body, [], c_style=True))
    return entities


def _entity(meta: dict, ports, block: str, calls: list[dict],
            c_style: bool = False) -> dict:
    name = meta["name"]
    sig = "SIG\x01" + "\x01".join([name] + [f"{p.source_type}:{p.name}" for p in ports])
    calls_key = "\x03".join(f"{c['callee']}({c['expr']})" for c in calls)
    return {
        "canonical_id": f"function:{name}",
        "name": name,
        "signature_hash": digest(sig),
        "ports_hash": digest(_ports_key(ports)),
        "calls_hash": digest(calls_key) if calls else None,
        "body_hash": digest("\n".join(_strip_c_comments(block).splitlines()))
                       if c_style else _fortran_body_hash(block),
        "calls": calls,
        "ports": [p.to_dict() for p in ports],
    }


# ---------------------------------------------------------------------------
# entity store (the incremental memory: per-file digest + entities)
# ---------------------------------------------------------------------------

def _store_path() -> Path:
    return SYNC_ROOT / "entity_store.json"


def load_store() -> dict:
    p = _store_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"fac_root": str(FAC_ROOT_DEFAULT), "digests": {}, "files": {}}


def save_store(store: dict) -> None:
    SYNC_ROOT.mkdir(parents=True, exist_ok=True)
    _store_path().write_text(json.dumps(store, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# delta computation
# ---------------------------------------------------------------------------

def compute_delta(root: Path, store: dict) -> tuple[list[str], list[dict], list[dict]]:
    """-> (changed_rels, file_units, entity_units).

    A comment-only edit yields changed_rels != [] but NO entity units (X1).
    """
    old_digests = store.get("digests", {})
    old_files = store.get("files", {})
    changed = []
    file_units: list[dict] = []
    for rel in PARSE_SET:
        path = root / rel
        if not path.exists():
            if rel in old_digests:
                changed.append(rel)
                file_units.append({"unit": "FILE_REMOVED", "file": rel})
            continue
        d = digest(path.read_text(errors="replace"))
        if old_digests.get(rel) != d:
            changed.append(rel)
            file_units.append({"unit": "FILE_ADDED" if rel not in old_digests
                               else "FILE_CHANGED", "file": rel})

    entity_units: list[dict] = []
    for rel in changed:
        new = {e["canonical_id"]: e for e in extract_file_entities(root, rel)}
        old = {e["canonical_id"]: e for e in old_files.get(rel, {}).get("entities", [])}
        for cid, e in new.items():
            if cid not in old:
                entity_units.append({"unit": "SYMBOL_ADDED", "canonical_id": cid,
                                     "file": rel, "symbol": e["name"]})
                continue
            o = old[cid]
            for key, unit in (("signature_hash", "SIGNATURE_CHANGED"),
                              ("ports_hash", "DATA_INTERFACE_CHANGED"),
                              ("calls_hash", "CALL_CHANGED")):
                if o.get(key) != e.get(key):
                    entity_units.append({"unit": unit, "canonical_id": cid,
                                         "file": rel, "symbol": e["name"]})
            if (o.get("body_hash") != e.get("body_hash")
                    and all(o.get(k) == e.get(k) for k in
                            ("signature_hash", "ports_hash", "calls_hash"))):
                entity_units.append({"unit": "CONTROL_CHANGED", "canonical_id": cid,
                                     "file": rel, "symbol": e["name"]})
        for cid in old.keys() - new.keys():
            entity_units.append({"unit": "SYMBOL_REMOVED", "canonical_id": cid,
                                 "file": rel, "symbol": old[cid].get("name", cid)})
    return changed, file_units, entity_units


def kernel_delta(root: Path, store: dict) -> tuple[list[str], list[dict], dict]:
    """Kernel-body evidence delta (K11 / §25): per-file kernel object hash.

    Cheap O(1) digest gate first; only digest-changed files are re-extracted.
    Comment-only edits do NOT shift the object hash (comments are stripped
    before scanning) -> no kernel unit -> X1 holds.  Semantic edits do.
    Returns (digest_changed_rels, units, parts_for_digest_changed).
    """
    kdig = store.get("kernel_digests", {})
    old_parts = store.get("kernel_parts", {})
    units, parts = [], {}
    digest_changed = []
    for rel in _krn.kernel_files(root):
        path = root / rel
        if not path.exists():
            continue
        d = digest(path.read_text(errors="replace"))
        if kdig.get(rel) != d:
            digest_changed.append((rel, d))
    for rel, d in digest_changed:
        part = _krn.objects_hash_for(rel, root)
        parts[rel] = part
        if old_parts.get(rel, {}).get("objects_hash") == part["objects_hash"]:
            continue                     # comment-only: no evidence delta
        units.append({"unit": "KERNEL_OBJECTS_CHANGED", "file": rel,
                      "objects_hash": part["objects_hash"],
                      "counts": part["counts"]})
    return [rel for rel, _ in digest_changed], units, parts


# ---------------------------------------------------------------------------
# projection registry: depends_on + status policy
# ---------------------------------------------------------------------------

PROJECTION_NAMES = ("function_data", "module_boundary", "flow_interface",
                    "semantic", "lvs", "frozen_topology", "kernel")

_AFFECT = {
    "FILE_ADDED": {"function_data", "module_boundary", "flow_interface",
                   "semantic", "frozen_topology", "kernel"},
    "FILE_REMOVED": {"function_data", "module_boundary", "flow_interface",
                     "semantic", "lvs", "frozen_topology", "kernel"},
    "FILE_CHANGED": {"function_data", "module_boundary", "flow_interface",
                     "semantic", "frozen_topology", "kernel"},
    "SYMBOL_ADDED": {"function_data", "module_boundary", "flow_interface",
                     "semantic", "lvs", "frozen_topology", "kernel"},
    "SYMBOL_REMOVED": {"function_data", "module_boundary", "flow_interface",
                       "semantic", "lvs", "frozen_topology", "kernel"},
    "SYMBOL_CHANGED": {"function_data", "module_boundary", "flow_interface",
                       "semantic", "frozen_topology", "kernel"},
    "SIGNATURE_CHANGED": {"function_data", "module_boundary", "flow_interface",
                          "semantic", "lvs", "frozen_topology", "kernel"},
    "DATA_INTERFACE_CHANGED": {"function_data", "module_boundary",
                               "flow_interface", "semantic", "frozen_topology",
                               "kernel"},
    "CALL_CHANGED": {"function_data", "module_boundary", "flow_interface",
                     "semantic", "frozen_topology", "kernel"},
    "CONTROL_CHANGED": {"frozen_topology", "kernel"},   # kernel ops/values
    "KERNEL_OBJECTS_CHANGED": {"kernel"},   # scrm.c body edits etc.
}


def affected_projections(units: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for u in units:
        for proj in _AFFECT.get(u["unit"], ()):
            out.setdefault(proj, []).append(u["unit"])
    return out


# ---------------------------------------------------------------------------
# artifact swap (atomic publish per file)
# ---------------------------------------------------------------------------

DI_FILES = ("fac_ports.json", "fac_shapes.json", "fac_bindings.json",
            "fac_transfers.json", "fac_module_interfaces.json",
            "fac_flow_interfaces.json", "fac_capability.json")
DI_PROJ_FILES = {"function_data": ("fac_ports.json", "fac_shapes.json",
                                   "fac_bindings.json", "fac_transfers.json"),
                 "module_boundary": ("fac_module_interfaces.json",),
                 "flow_interface": ("fac_flow_interfaces.json",)}
SEM_FILES = ("fac_semantic_flows.json", "fac_semantic_stages.json",
             "fac_memberships.json", "fac_semantic_edges.json",
             "fac_semantic_audit.json")
KERNEL_FILES = list(_krn.KERNEL_FILES)
KERNEL_NAMES = tuple(_krn.KERNEL_NAMES)


def _os_replace(src: Path, dst: Path) -> None:
    import os
    os.replace(str(src), str(dst))


def _swap(src_dir: Path, dst_dir: Path, names: list[str]) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        src = src_dir / n
        if not src.exists():
            continue
        tmp = dst_dir / (n + ".swp")
        shutil.copyfile(src, tmp)
        _os_replace(tmp, dst_dir / n)


def _snapshot(dst: Path, names: list[str]) -> dict[str, bytes | None]:
    return {n: (dst / n).read_bytes() if (dst / n).exists() else None for n in names}


# ---------------------------------------------------------------------------
# LVS annotation (selective invalidation; Design plane untouched)
# ---------------------------------------------------------------------------

def annotate_lvs(units: list[dict], revision: str) -> dict:
    """Selective LVS invalidation for signature/symbol-changed code objects.

    Only code objects actually bound by the Design plane are marked STALE
    (re-verification needs the joern graph — outside the file-driven lane).
    Changed symbols NOT referenced by the design are UNBOUND: LVS is
    provably unaffected.  Design plane is never touched (XS7).
    """
    if not LVS_OUT.exists():
        return {"status": "SKIPPED", "reason": "lvs artifact missing"}
    raw = json.loads(LVS_OUT.read_text())
    bound = {((bd.get("code_object") or "").split("::")[-1])
             for bd in raw.get("block_diffs", [])}
    changed_syms = sorted({u["symbol"] for u in units
                           if u["unit"] in ("SIGNATURE_CHANGED", "SYMBOL_ADDED",
                                            "SYMBOL_REMOVED")})
    if not changed_syms:
        return {"status": "UNCHANGED", "reason": "no signature-level unit",
                "revision": revision}
    annotations = {}
    for s in changed_syms:
        annotations[s] = "STALE" if any(s in b for b in bound) else "UNBOUND"
    n = 0
    for s, st in annotations.items():
        if st != "STALE":
            continue
        for bd in raw.get("block_diffs", []):
            if s in (bd.get("code_object") or "") and bd.get("status") != "STALE":
                bd["status"] = "STALE"
                bd["sync_note"] = (f"signature/symbol changed in revision "
                                   f"{revision}; re-verification outside sync lane")
                n += 1
    if not n:
        return {"status": "UNCHANGED", "reason": "no design-bound symbol changed",
                "annotations": annotations, "revision": revision}
    raw["_sync_revision"] = revision
    raw["by_status"] = {}
    for bd in raw.get("block_diffs", []):
        st = bd.get("status")
        raw["by_status"][st] = raw["by_status"].get(st, 0) + 1
    raw["overall_status"] = ("STALE" if any(b.get("status") in ("STALE", "MISMATCH")
                                            for b in raw.get("block_diffs", []))
                             else raw.get("overall_status", "UNKNOWN"))
    LVS_OUT.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    return {"status": "RECOMPUTED", "changed": n,
            "annotations": annotations, "revision": revision}


# ---------------------------------------------------------------------------
# semantic staleness (XS6: human overlay preserved, marked STALE)
# ---------------------------------------------------------------------------

def mark_stale_semantic(revision: str) -> dict:
    """Stages the human accepted/rejected -> STALE_NEEDS_REVIEW (overlay is
    reapplied by the API on regeneration; never rewritten by sync)."""
    edits_path = SEM_OUT / "fac_user_edits.json"
    if not edits_path.exists():
        return {"marked": 0}
    try:
        edits = json.loads(edits_path.read_text()) or {}
    except json.JSONDecodeError:
        edits = {}
    stages = json.loads((SEM_OUT / "fac_semantic_stages.json").read_text())
    by_id = {s["semantic_stage_id"]: s for s in stages}
    stale = {}
    for sid, ed in edits.items():
        s = by_id.get(sid)
        if not s:
            continue
        if ed.get("status") in ("ACCEPTED", "REJECTED"):
            stale[sid] = {"human_status": ed.get("status"),
                          "sync_status": "STALE_NEEDS_REVIEW",
                          "since_revision": revision}
    if stale:
        (SEM_OUT / "fac_sync_staleness.json").write_text(
            json.dumps(stale, ensure_ascii=False, indent=2))
    return {"marked": len(stale)}


def _support_refs_or_none() -> dict | None:
    p = KERNEL_OUT / "support_refs.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# consistency audit (XS4)
# ---------------------------------------------------------------------------

def check_consistency(revision: str) -> dict:
    di = json.loads((DI_OUT / "fac_ports.json").read_text())
    shapes = {p["port_id"] for p in
              json.loads((DI_OUT / "fac_shapes.json").read_text())}
    got = {f["name"] for f in di}
    checks = []
    for f in di:
        fid = {p["port_id"] for p in f["ports"]}
        checks.append({"check": f"port-ids-of-{f['name']} ⊆ shapes", "ok": fid <= shapes})
    flow = json.loads((DI_OUT / "fac_flow_interfaces.json").read_text())
    bad = [c for entry in flow.values() for c in entry.get("callees", [])
           if c not in got]
    checks.append({"check": "flow-interface callees ⊆ DI functions", "ok": not bad,
                   "bad": bad[:5]})
    memb = json.loads((SEM_OUT / "fac_memberships.json").read_text())
    missing = [m["id"] for m in memb
               if m["member_kind"] == "canonical_symbol"
               and m["id"].removeprefix("function:") not in got]
    checks.append({"check": "semantic members ⊆ DI functions", "ok": not missing,
                   "missing": missing[:5]})
    mods = json.loads((DI_OUT / "fac_module_interfaces.json").read_text())
    allnames = {p["name"] for f in di for p in f["ports"]}
    missmod = [n for m in mods.values() for p in m
               for n in [p["name"]] if n not in allnames]
    checks.append({"check": "module port names ⊆ DI port names", "ok": not missmod,
                   "missing": missmod[:5]})
    # kernel artifacts (projection kernel): present + port support refs valid
    kern = KERNEL_OUT / "fac_kernel.json"
    if kern.exists():
        kb = json.loads(kern.read_text())
        syms = {s.get("symbol_id"): s for s in kb.get("symbols", [])}
        param_ids = {s.get("symbol_id") for s in syms.values()
                     if s.get("role") == "PARAMETER"}
        refs = _support_refs_or_none()
        badk = [r["kernel_symbol_id"] for r in (refs or [])["ports"]
                if r["kernel_symbol_id"] not in param_ids]
        checks.append({"check": "support-refs ports ⊆ kernel parameter symbols",
                       "ok": not badk, "bad": badk[:5]})
    ok = all(c["ok"] for c in checks)
    return {"revision_id": revision, "consistent": ok,
            "checks": [c for c in checks if not c["ok"]] or checks,
            "total": len(checks)}


# ---------------------------------------------------------------------------
# the sync pipeline
# ---------------------------------------------------------------------------

def _write_json(target: Path, data) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _os_replace(tmp, target)


def current_state() -> dict:
    p = SYNC_ROOT / "state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"revision_id": BASE_REVISION, "based_on_revision": None,
            "projections": {}, "changed_files": [], "entity_units": [],
            "file_units": [], "synced_at": None, "failed": None,
            "fac_root": str(FAC_ROOT_DEFAULT)}


def sync(*, root: Path | None = None, force: bool = False,
         regenerate: bool = False) -> dict:
    """Incremental sync of the tracked FAC parse set under `root`.

    regenerate: re-extract every file (full-rebuild baseline — only used for
    the performance comparison, §34); force: recompute all projections even on
    an empty delta (baseline build / restore after experiments).
    """
    t0 = time.time()
    root = Path(root or FAC_ROOT_DEFAULT)
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    SYNC_ROOT.mkdir(parents=True, exist_ok=True)
    store = load_store()
    state = current_state()

    # ---- 1. delta ---------------------------------------------------------
    if regenerate:
        changed = [r for r in PARSE_SET if (root / r).exists()]
        file_units = [{"unit": "FILE_CHANGED", "file": r} for r in changed]
        _c, _fu, entity_units = compute_delta(root, store)
    else:
        changed, file_units, entity_units = compute_delta(root, store)
    kern_changed, kern_units, kern_parts = kernel_delta(root, store)
    units = file_units + entity_units + kern_units
    aff = affected_projections(units) if units else {}

    if not changed and not kern_changed and not force and not regenerate:
        # X1: nothing at all — no new revision, nothing recomputed
        return {"revision_id": state.get("revision_id"), "delta": "empty",
                "changed_files": [], "saved": False,
                "projections": state.get("projections", {})}
    if not entity_units and not kern_units and not force and not regenerate:
        # X1: file digest changed but NO evidence delta (e.g. comment-only):
        # re-index the digests/entities so the change is acknowledged, but
        # publish NO revision — projections are provably unaffected.
        for rel in changed:
            p = root / rel
            if p.exists():
                store["digests"][rel] = digest(p.read_text(errors="replace"))
                store["files"][rel] = {"entities": extract_file_entities(root, rel)}
            else:
                store["digests"].pop(rel, None)
                store["files"].pop(rel, None)
        for rel in kern_changed:
            store.setdefault("kernel_parts", {})[rel] = kern_parts[rel]
        save_store(store)
        return {"revision_id": state.get("revision_id"), "delta": "empty_evidence",
                "changed_files": changed, "saved": False,
                "projections": state.get("projections", {})}

    rev = state.get("revision_id", BASE_REVISION)
    if rev == BASE_REVISION and not changed and not force and not regenerate:
        revision_id = BASE_REVISION
    else:
        revision_id = f"r{int(rev[1:]) + 1}" if rev.startswith("r") else f"r1"

    proj_status: dict[str, dict] = {}
    try:
        # ---- 2. recompute affected projections (staged) -------------------
        staging = SYNC_ROOT / "staging" / revision_id
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        do_di = bool({"function_data", "module_boundary", "flow_interface"}
                     & set(aff)) or force or regenerate
        do_sem = "semantic" in aff or force or regenerate
        do_krn = "kernel" in aff or force or regenerate
        files_parsed = 0
        if do_di:
            shutil.copy(DI_OUT / "fac_ports.json", staging / "fac_ports.json")
            scoped = set(changed) if (changed and not regenerate) else None
            files_parsed = len(scoped) if scoped else len(PARSE_SET)
            _di.run(fac_root=root, scoped_files=scoped, out=staging)
        if do_krn:
            # kernel must run BEFORE semantic: CRM stage edges are derived
            # from the resolved kernel CALL index (same staged revision)
            _krn.run(fac_root=root, out=staging, revision=revision_id)
        if do_sem:
            # semantics read the UPDATED data-interface plane (staging when
            # regenerated, canonical otherwise) + frozen flow_infer regions
            # + the staged (or canonical) kernel index for CRM CALL edges
            _fs.run(out=staging, di=staging if do_di else DI_OUT, fin=FIN,
                    ker=staging if do_krn else KERNEL_OUT)

        # ---- 3. staged -> canonical (atomic swap), compare old vs new -----
        pre_di = _snapshot(DI_OUT, DI_FILES)
        pre_sem = _snapshot(SEM_OUT, SEM_FILES)
        pre_krn = _snapshot(KERNEL_OUT, KERNEL_NAMES)
        if do_di:
            _swap(staging, DI_OUT, DI_FILES)
        if do_sem:
            _swap(staging, SEM_OUT, SEM_FILES)
            mark_stale_semantic(revision_id)
        if do_krn:
            _swap(staging, KERNEL_OUT, KERNEL_NAMES)
        post_di = _snapshot(DI_OUT, DI_FILES)
        post_sem = _snapshot(SEM_OUT, SEM_FILES)
        post_krn = _snapshot(KERNEL_OUT, KERNEL_NAMES)

        def _changed(names: tuple[str, ...]) -> bool:
            return any(pre_di.get(n) != post_di.get(n) for n in names)

        sem_changed = any(pre_sem.get(n) != post_sem.get(n) for n in SEM_FILES)
        kern_changed_by_bytes = any(pre_krn.get(n) != post_krn.get(n)
                                    for n in KERNEL_NAMES)

        lvs_note: dict
        if state.get("revision_id") == BASE_REVISION:
            # baseline build: never annotate a fresh verifier output
            if LVS_OUT.exists():
                shutil.copyfile(LVS_OUT, SYNC_ROOT / "lvs_baseline.json")
            lvs_note = {"status": "SKIPPED",
                        "reason": "baseline build; no invalidation (design untouched)"}
        else:
            lvs_note = annotate_lvs(units, revision_id) if (
                "lvs" in aff or force or regenerate) else {"status": "SKIPPED"}

        # ---- 4. projection statuses ---------------------------------------
        for p in PROJECTION_NAMES:
            base = {"based_on_revision": revision_id}
            if p == "frozen_topology":
                proj_status[p] = {**base,
                                  "status": ("STALE_NOT_RECOMPUTED" if (
                                      force or regenerate or "frozen_topology" in aff)
                                      else "UNCHANGED"),
                                  "reason": "TOPO-ENGINE0 lane frozen; STALE marked, "
                                            "never recomputed by sync"}
            elif p == "lvs":
                proj_status[p] = {**base,
                                  "status": lvs_note.get("status", "UNCHANGED"),
                                  "detail": lvs_note.get("reason"),
                                  "changed": lvs_note.get("changed", 0)}
            elif p in ("function_data", "module_boundary", "flow_interface"):
                if p in aff or force or regenerate:
                    proj_status[p] = {**base,
                                      "status": ("RECOMPUTED" if _changed(
                                          DI_PROJ_FILES[p]) else "UNCHANGED"),
                                      "regenerated": True}
                else:
                    proj_status[p] = {**base, "status": "UNCHANGED",
                                      "based_on_revision": rev}
            elif p == "semantic":
                if do_sem:
                    proj_status[p] = {**base,
                                      "status": ("RECOMPUTED" if sem_changed
                                                 else "UNCHANGED"),
                                      "regenerated": True,
                                      "stale_marked": _sem_stale_count()}
                else:
                    proj_status[p] = {**base, "status": "UNCHANGED",
                                      "based_on_revision": rev}
            elif p == "kernel":
                if do_krn:
                    proj_status[p] = {**base,
                                      "status": ("RECOMPUTED" if kern_changed_by_bytes
                                                 else "UNCHANGED"),
                                      "regenerated": True}
                else:
                    proj_status[p] = {**base, "status": "UNCHANGED",
                                      "based_on_revision": rev}

        # ---- 5. audits + state (commit record LAST) -----------------------
        delta_rec = {"revision_id": revision_id, "changed_files": changed,
                     "file_units": file_units, "entity_units": entity_units,
                     "kernel_units": kern_units,
                     "affected_projections": aff}
        _write_json(SYNC_ROOT / "deltas" / f"{revision_id}.json", delta_rec)
        _append_json(SYNC_ROOT / "invalidation_audit.json",
                     {"revision_id": revision_id, **delta_rec,
                      "projection_status": proj_status})
        _append_json(SYNC_ROOT / "performance.json", {
            "revision_id": revision_id,
            "mode": "full" if regenerate else "incremental",
            "changed_files": len(changed), "files_parsed": files_parsed,
            "projections_recomputed": sum(
                1 for p in proj_status.values() if p["status"] == "RECOMPUTED"),
            "wall_ms": round((time.time() - t0) * 1000, 1)})
        _append_json(SYNC_ROOT / "revision_audit.json", {
            "revision_id": revision_id, "based_on_revision": rev,
            "changed_files": changed, "projections": proj_status,
            "lvs": lvs_note, "wall_ms": round((time.time() - t0) * 1000, 1)})
        consistency = check_consistency(revision_id)
        _append_json(SYNC_ROOT / "consistency_audit.json", consistency)

        new_store = {"fac_root": str(root), "digests": {}, "files": {}}
        for rel in PARSE_SET:
            p = root / rel
            if not p.exists():
                continue
            new_store["digests"][rel] = digest(p.read_text(errors="replace"))
            new_store["files"][rel] = {
                "entities": extract_file_entities(root, rel) if rel in changed
                else store.get("files", {}).get(rel, {}).get("entities", [])}
        new_store["kernel_digests"] = {}
        new_store["kernel_parts"] = {}
        for rel in _krn.kernel_files(root):
            p = root / rel
            if not p.exists():
                continue
            new_store["kernel_digests"][rel] = digest(p.read_text(errors="replace"))
            if rel in kern_changed:
                new_store["kernel_parts"][rel] = _krn.objects_hash_for(rel, root)
            elif ("kernel_parts" in store and rel in store["kernel_parts"]):
                new_store["kernel_parts"][rel] = store["kernel_parts"][rel]
        save_store(new_store)

        new_state = {
            "revision_id": revision_id, "based_on_revision": rev,
            "fac_root": str(root), "changed_files": changed,
            "file_units": file_units, "entity_units": entity_units,
            "projections": proj_status,
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "failed": None,
        }
        _write_json(SYNC_ROOT / "state.json", new_state)
        return new_state
    except Exception as exc:                        # noqa: BLE001
        fail = dict(state)
        fail["failed"] = {"error": str(exc),
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json(SYNC_ROOT / "state.json", fail)
        _append_json(SYNC_ROOT / "issue_register.json", {
            "revision": revision_id, "phase": "sync", "error": str(exc)})
        return fail


def _sem_stale_count() -> int:
    p = SEM_OUT / "fac_sync_staleness.json"
    if not p.exists():
        return 0
    return len(json.loads(p.read_text()))


def _append_json(path: Path, rec: dict) -> dict:
    recs = []
    if path.exists():
        try:
            recs = json.loads(path.read_text()) or []
        except json.JSONDecodeError:
            recs = []
    recs.append(rec)
    _write_json(path, recs)
    return rec


# ---------------------------------------------------------------------------
# worktree helpers for the E1-E5 experiments
# ---------------------------------------------------------------------------

def prepare_worktree(dst: Path | None = None, fac_root: Path | None = None) -> Path:
    dst = Path(dst or "/tmp/fac-sync-wt")
    fac_root = Path(fac_root or FAC_ROOT_DEFAULT)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(fac_root, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return dst


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        st = sync()
        print(json.dumps({k: st.get(k) for k in
                          ("revision_id", "based_on_revision", "changed_files",
                           "failed")}, ensure_ascii=False, indent=2))
        print("projections:", json.dumps(st.get("projections", {}),
                                         ensure_ascii=False, indent=2))
