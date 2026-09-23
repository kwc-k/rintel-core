"""Entry / dispatch / guard recovery (FLOW-INFER0 §5-§7, §11).

Evidence model (honest):

  * The FAC drivers are command interpreters: `main -> Initxx -> SetModName
    -> ParseArgs(argc, argv, methods)`.  `methods` is a per-app registry
    (dispatch table).  "Calculation modes" are NOT compile-time flags in
    this codebase: every branch is input/command driven.
  * Guard classification (never guesses truth):
      - dispatch guard (command name ∈ table)  → static under a scenario
        constraint (TRUE for the constrained family, FALSE for other
        known families, UNKNOWN for anything not resolvable)
      - arg count / arg type guards            → RUNTIME_DEPENDENT
      - any other condition                    → UNKNOWN unless provable
"""
from __future__ import annotations

from pathlib import Path

from .facts import FacFacts, APPS

# names that mark input-parameter guards (honest RUNTIME_DEPENDENT markers)
INPUT_TOKENS = (
    "argc", "argv", "argt", "arg", "tp", "ng", "n0", "n1", "nti", "at[",
    "fgets", "getline", "token", "TKLIST", "nargs",
)

# dispatch-family evidence: method-name prefixes found in the registry
FAMILY_PREFIXES = [
    "Beta",
    "RMatrix", "CI", "Config", "Excitation", "Ionization", "Recombination",
    "Reinit", "CETable", "CX", "SetCE", "SetCI", "SetRG", "SetAngZ",
    "SetAtom", "SetU", "SetT", "SetS", "SetAngle", "SetBorn", "SetCEPW",
    "SetBasis", "SetExtra", "SetSlater", "SetMixed", "SetAvg", "Scale",
    "Slater", "Structure", "Transition", "Rec", "Collapse", "Combine",
    "Append", "Join", "Preload", "Free", "Prepare", "Avg", "Angular",
    "Radial", "Orbital", "Optimize", "Average", "Interp", "Maxwell",
]


def app_of_file(rel: str) -> str | None:
    for app, info in APPS.items():
        if info["file"] == rel:
            return app
    return None


def find_main(facts: FacFacts, rel: str, app: str) -> dict | None:
    """Entry symbol: main of this file (frozen symbol; id may be shared)."""
    nodes = [n for n in facts.symbols.get("main", []) if (n.get("file") or "").replace("./", "") == Path(rel).name]
    return nodes[0] if nodes else None


def find_dispatch(facts: FacFacts, app: str) -> tuple[str | None, list[dict]]:
    """(dispatcher symbol name, entry→dispatcher call facts)."""
    calls = facts.callee_edges("main")
    for e in calls:
        tgt = (e.get("target") or "").split(":")[-1]
        if tgt == "ParseArgs":
            return "ParseArgs", [e]
    return None, []


def method_family(name: str) -> str | None:
    """Best-effort evidence-based family key from the registry name.

    Only used when >=2 methods share the prefix — a naming convention in
    the source, never invented physics semantics.
    """
    for p in FAMILY_PREFIXES:
        if name.startswith(p):
            return p
    return None


def classify_guard(expr: str, kind: str) -> tuple[str, list[str]]:
    """→ (resolution, why).  FI3: UNKNOWN ≠ FALSE, RUNTIME_DEPENDENT ≠ FALSE."""
    import re as _re
    if kind == "switch":
        var = body0(expr)
        if any(var.startswith(t) for t in INPUT_TOKENS):
            return "RUNTIME_DEPENDENT", [f"switch on input-derived variable '{var}'"]
        return "UNKNOWN", [f"switch on '{var}': value depends on runtime state, not statically resolvable"]
    if kind == "case":
        return "RUNTIME_DEPENDENT", ["case selection depends on the switch value (input/runtime)"]
    cond = expr
    idents = {m.group(1) for m in _re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", cond)}
    idents -= {"if", "return", "else", "sizeof", "NULL", "free"}
    # strict: only explicit input tokens are provably runtime-dependent;
    # everything else stays UNKNOWN (never guessed FALSE/TRUE).
    if any(v in INPUT_TOKENS or v.startswith(("argc", "argv", "argt", "ng", "tp")) for v in idents):
        return "RUNTIME_DEPENDENT", [
            f"condition tests command/input-derived variable(s): {sorted(idents)[:6]}",
        ]
    if "strcmp(" in cond or "strncmp(" in cond or "atoi(" in cond or "atof(" in cond:
        return "RUNTIME_DEPENDENT", ["condition compares input/config-derived strings (strcmp family)"]
    mode_like = idents & {"mode", "cmd", "command", "key", "task", "qkey", "opt", "option"}
    if mode_like:
        return "RUNTIME_DEPENDENT", [f"condition tests config/mode flag(s): {sorted(mode_like)}"]
    return "UNKNOWN", ["condition depends on runtime values (computed state, config, data); not statically resolvable"]


def body0(expr: str) -> str:
    return expr.strip().split(".")[0].split("(")[0].strip()


def dispatch_guards(facts: FacFacts, app: str) -> list[dict]:
    """Guards at the dispatch level: command-name membership in families.

    These are the ONLY statically evaluable guards in FAC: the scenario
    constraint names a method family; the dispatch guard then resolves
    TRUE (constrained), FALSE (other known family) or UNKNOWN.
    """
    table = facts.method_tables()
    rel = APPS[app]["file"]
    out = []
    for name, handler, i in table.get(rel, []):
        fam = method_family(name)
        out.append({
            "guard_id": f"g-dispatch:{app}:{name}",
            "source_fact_id": f"dispatch-table:{rel}:{i + 1}",
            "expression": f"command == '{name}'",
            "variables": ["command"],
            "scope": "dispatch",
            "truth_class": "DERIVED",
            "resolution": "UNKNOWN",          # resolved per scenario
            "source": {"file": rel, "line": i + 1},
            "why": [f"registry entry at {rel}:{i + 1}; command-name guard"],
            "family": fam,
        })
    return out
