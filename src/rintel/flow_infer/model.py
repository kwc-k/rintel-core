"""Flow-Infer domain model (FLOW-INFER0 §3).

All objects are DERIVED/PROJECTED views; they never live in the canonical
evidence store.  Serialisation is plain dicts (JSON artifacts, §27).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# minimal fields (§3) — renderer/domain-neutral
# ---------------------------------------------------------------------------

@dataclass
class FlowGuard:
    guard_id: str
    source_fact_id: str | None
    expression: str
    variables: list[str]
    scope: str                       # 'dispatch' | 'input' | 'state' | 'config' | 'program'
    truth_class: str                 # always OBSERVED/DERIVED at guard level
    resolution: str                  # TRUE | FALSE | UNKNOWN | RUNTIME_DEPENDENT
    source: dict | None = None       # {file, line}
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowEdge:
    edge_id: str
    source: str                      # node id
    target: str                      # node id
    kind: str                        # 'flow' | 'guard' | 'boundary' | 'shared'
    guard_id: str | None = None
    truth_class: str = 'DERIVED'     # DERIVED / OBSERVED-backed
    target_resolution: str = 'EXACT'
    witnesses: list[dict] = field(default_factory=list)   # call facts
    resolution: str = 'ACTIVE'       # ACTIVE | INACTIVE | MAY | UNKNOWN (scenario projection)
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowNode:
    node_id: str
    kind: str                        # entry|init|dispatch|command|region|shared|guard|exit|unknown|boundary
    label: str
    app: str | None = None
    members: list[str] = field(default_factory=list)   # canonical symbol ids / command names
    count: int | None = None
    truth_class: str = 'DERIVED'
    capability: str | None = None    # e.g. PARTIAL for unresolved callees
    why: list[str] = field(default_factory=list)
    witness_count: int = 0
    representative_witnesses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowRegion:
    region_id: str
    name: str
    app: str
    methods: list[str]               # dispatch command names
    handler_symbols: list[str]       # P* handlers (canonical ids)
    callees: list[str]               # union of callee names (resolved + call-expr names)
    member_symbols: list[str]        # canonical symbol ids inside the region
    shared_with: list[str] = field(default_factory=list)   # shared core memberships
    derived: bool = True
    why: list[str] = field(default_factory=list)
    witnesses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowMembership:
    symbol_id: str
    symbol_name: str
    file: str | None
    scenario_ids: list[str]
    region_ids: list[str]
    kind: str = 'single'             # 'shared' when called from >=2 regions
    witnesses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlowScenario:
    scenario_id: str
    name: str
    app: str
    constraints: list[str]           # e.g. ['command_family: RMatrix*']
    active_regions: list[str]
    inactive_regions: list[str]
    unknown_regions: list[str]
    shared_regions: list[str]
    witnesses: list[dict] = field(default_factory=list)
    guard_resolutions: list[dict] = field(default_factory=list)
    coverage: str = 'PARTIAL'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MasterFlow:
    flow_id: str
    name: str
    app: str
    entry_symbols: list[str]
    exit_symbols: list[str]
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    regions: list[dict]
    scenario_ids: list[str]
    witnesses: list[dict]
    truth_summary: dict
    capability_summary: dict
    guards: list[dict]
    derived: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d
