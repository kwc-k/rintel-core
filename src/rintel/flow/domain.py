"""P2-FLOW0 software-circuit domain helpers (pure, no I/O; SPEC-P2 §2-§16).

Frozen semantics:
- Flow is NOT evidence (§2.1): it is a visual projection / design view over
  the evidence graph.  Auto-created content (blocks/ports/nets) is derived
  from evidence; everything a user adds is *design* until writeback turns it
  into code + a new evidence snapshot (§13).
- Renderer-neutral (§2.2): this module (and the store) hold the domain model;
  the X6 adapter lives in the web app only.
- Renderer-neutral nets: a `control` net between two existing blocks is the
  projection of a real CALLS edge.  `data` nets are design intent and are
  NEVER auto-generated (§8 correctness boundary) and NEVER compared to
  evidence by the LVS-like validator.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

BLOCK_KINDS = ("function", "object", "composite", "proposed")
BLOCK_STATES = ("existing", "modified", "proposed")
PORT_DIRECTIONS = ("input", "output")
SEMANTIC_KINDS = ("data", "control", "event", "error", "resource")
NET_KINDS = ("data", "control", "event", "error", "resource")
BINDING_KINDS = ("implementation",)
FLOW_STATUSES = ("active", "archived")

# evidence node kind -> flow block kind (projection mapping, §3.2 / §6)
SYMBOL_TO_BLOCK_KIND = {
    "FUNCTION": "function",
    "METHOD": "function",
    "SUBROUTINE": "function",
    "PROGRAM": "function",
    "INTERFACE": "function",
    "CLASS": "object",
    "TYPE": "object",
    "MODULE": "object",
    "SUBMODULE": "object",
    "PACKAGE": "object",
}
PROJECTABLE_SYMBOL_KINDS = tuple(SYMBOL_TO_BLOCK_KIND)

# LVS-like statuses (§16)
VALID_MATCH = "MATCH"
VALID_STALE = "STALE"
VALID_MISMATCH = "MISMATCH"
VALID_UNBOUND = "UNBOUND"
VALID_STATUSES = (VALID_MATCH, VALID_STALE, VALID_MISMATCH, VALID_UNBOUND)

_UNKNOWN = "unknown"

_CTRL_IN = {"name": "control_in", "direction": "input",
            "semantic_kind": "control", "code_type": None}
_CTRL_OUT = {"name": "control_out", "direction": "output",
             "semantic_kind": "control", "code_type": None}


def control_ports() -> list[dict]:
    """Implicit control ports anchoring CALLS-projected nets (§8).

    Every function/object block carries a control_in + control_out port so a
    `control` net always has concrete endpoints; they are not user-editable
    signature data.
    """
    return [dict(_CTRL_IN), dict(_CTRL_OUT)]


def signature_ports(params: list[dict], return_info: Optional[dict]) -> list[dict]:
    """Signature-derived data ports (§6/§7).

    ``params``: [{"name": str, "type": str|None, "has_type": bool}].
    ``return_info``: {"type": str|None} or None (no return).  Unknown types
    are represented as ``code_type=None`` and *displayed* as `unknown` —
    never guessed (§6 rule 4).
    """
    ports: list[dict] = []
    for i, p in enumerate(params):
        ports.append({
            "name": p["name"],
            "direction": "input",
            "semantic_kind": "data",
            "code_type": p.get("type") if p.get("type") else None,
            "position_order": i,
        })
    if return_info is not None:
        ports.append({
            "name": "result",
            "direction": "output",
            "semantic_kind": "data",
            "code_type": return_info.get("type") if return_info.get("type")
            else None,
            "position_order": 0,
        })
    return ports


def block_ports(params: list[dict], return_info: Optional[dict]) -> list[dict]:
    """Full default port set for a symbol-derived block (data + control)."""
    return signature_ports(params, return_info) + control_ports()


def type_display(code_type: Optional[str]) -> str:
    """Display type: unknown when no type is known (no guessing)."""
    return code_type if code_type else _UNKNOWN


# ---------------------------------------------------------------------------
# Python writeback template (§12; FLOW0 fixture language = python)
# ---------------------------------------------------------------------------

_PY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def py_func_template(name: str, input_names: list[str]) -> str:
    """Deterministic template for a proposed Python function (§12)."""
    args = ", ".join(n for n in input_names if _PY_NAME_RE.match(n))
    if not args:
        args = ""
    return (f"def {name}({args}):\n"
            f"    \"\"\"{name}: FLOW0 proposed block (writeback).\"\"\"\n"
            f"    raise NotImplementedError\n")


def python_arg_name(port_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", port_name)
    if not safe or safe[0].isdigit():
        safe = f"p_{safe}"
    return safe


# ---------------------------------------------------------------------------
# Patch preview (§14) — generated from the SAME content the apply step writes
# ---------------------------------------------------------------------------

def new_file_diff(stem: str, relpath: str, content: str) -> str:
    """Unified diff for a newly created file (a/{stem} <- /dev/null)."""
    old_lines: list[str] = []
    new_lines = content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile="/dev/null", tofile=f"b/{relpath}",
                                n=3)
    return "".join(diff)


def replace_diff(relpath: str, old_content: str, new_content: str) -> str:
    """Unified diff of a same-file edit (a/{relpath} -> b/{relpath})."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile=f"a/{relpath}",
                                tofile=f"b/{relpath}", n=3)
    return "".join(diff)


def ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


# ---------------------------------------------------------------------------
# LVS-like consistency (§16)
# ---------------------------------------------------------------------------

def compare_flow(blocks: dict, bindings: dict, port_block: dict,
                 nets: list[dict], evidence_call_pairs: set,
                 node_exists: set, port_children: Optional[dict] = None,
                 port_parents: Optional[dict] = None) -> dict:
    """Minimal software-LVS: Flow existing blocks vs current evidence.

    Inputs (all store-shaped dicts):
    - ``blocks``: {block_id: {"kind","state"}}
    - ``bindings``: {block_id: binding-row} (only bound blocks participate)
    - ``port_block``: {port_id: block_id}
    - ``nets``: [{source_port_id,target_port_id,kind}]
    - ``evidence_call_pairs``: {(src_canonical, dst_canonical)} — CALLS edges
      of the current evidence snapshot restricted to the flow's symbols
    - ``node_exists``: {canonical_id} — cannonical ids present in the current
      evidence snapshot
    - ``port_children``/``port_parents``: composite unrolling maps
      ({composite_port_id: [child_port_id]}), derived from internal nets of
      composites (a composite input port feeds its children; child output
      ports feed the composite output port).

    Checks (§16): binding still exists; expected CALLS still exists; new
    unexpected CALLS; missing CALLS.  Only **control** nets are CALLS
    projections; data/event/error/resource nets are design-only and are
    excluded (never compare design intent to evidence — §8).  Control nets
    crossing a composite boundary are unrolled through the composite so
    expected CALLS pairs stay block-to-block.

    Status priority: UNBOUND > STALE (missing) > MISMATCH (unexpected) >
    MATCH.  Detail lists both missing and unexpected when both exist.
    """
    pc = port_children or {}
    pp = port_parents or {}

    def _unroll_target(port_ids: list[str],
                       depth: int = 0) -> set[str]:
        """Blocks reached by nets into these ports (following composite
        input continuations)."""
        out: set[str] = set()
        if depth > 8:
            return out
        for pid in port_ids:
            children = pc.get(pid)
            if children:
                for c in children:
                    out |= _unroll_target([c], depth + 1)
            else:
                b = port_block.get(pid)
                if b is not None:
                    out.add(b)
        return out

    def _unroll_source(port_ids: list[str],
                       depth: int = 0) -> set[str]:
        """Blocks driving these ports (following composite output
        consolidations)."""
        out: set[str] = set()
        if depth > 8:
            return out
        for pid in port_ids:
            parents = pp.get(pid)
            if parents:
                for c in parents:
                    out |= _unroll_source([c], depth + 1)
            else:
                b = port_block.get(pid)
                if b is not None:
                    out.add(b)
        return out

    unbound: list[dict] = []
    for bid, b in blocks.items():
        # Only bind-able blocks (function/object) participate in the LVS
        # check; composite is pure structure and proposed is design —
        # neither claims an evidence binding.
        if b.get("kind") not in ("function", "object"):
            continue
        if b.get("state") not in ("existing", "modified"):
            continue
        binding = bindings.get(bid)
        if not binding:
            unbound.append({"block_id": bid, "block_name": b.get("name", ""),
                            "reason": "no_binding"})
            continue
        cid = binding.get("canonical_symbol_id")
        if cid not in node_exists:
            unbound.append({"block_id": bid, "block_name": b.get("name", ""),
                            "reason": "symbol_missing",
                            "canonical_symbol_id": cid})

    expected: set = set()
    for net in nets:
        if net.get("kind") != "control":
            continue
        srcs = _unroll_source([net["source_port_id"]])
        dsts = _unroll_target([net["target_port_id"]])
        for s in srcs:
            sb = bindings.get(s)
            if not sb:
                continue
            for d in dsts:
                db = bindings.get(d)
                if not db:
                    continue  # proposed/composite endpoint: no canonical pair
                expected.add((sb["canonical_symbol_id"],
                              db["canonical_symbol_id"]))

    missing = sorted(expected - evidence_call_pairs)
    unexpected = sorted(evidence_call_pairs - expected)

    if unbound:
        status = VALID_UNBOUND
    elif missing:
        status = VALID_STALE
    elif unexpected:
        status = VALID_MISMATCH
    else:
        status = VALID_MATCH

    return {
        "status": status,
        "unbound": unbound,
        "missing_calls": [{"src": s, "dst": d} for s, d in missing],
        "unexpected_calls": [{"src": s, "dst": d} for s, d in unexpected],
        "expected_call_pairs": len(expected),
        "checked_bindings": len(bindings),
    }
