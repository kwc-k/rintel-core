"""SOFTWARE-DRC0 rule package (model is in rintel.drc)."""
from __future__ import annotations

from . import boundary, control, data, ports, resource, state, uncertainty

# deterministic rule order (stable runs); instantiated once
_RULE_CLASSES = [
    *ports.ALL,          # P001–P004
    *data.ALL,           # D001–D003
    *state.ALL,          # S001–S003
    *control.ALL,        # C001–C003
    *resource.ALL,       # R001–R003
    *uncertainty.ALL,    # U001–U003
    *boundary.ALL,       # B001–B004 (CandidateBoundaryDRC, §8)
]
RULES = [cls() for cls in _RULE_CLASSES]
