"""SYNTHESIS0 rule registry (spec §10: design-diff → code-change mapping).

Every mapping carries a rule id; supported rules are the bounded
transforms, everything else stays PLAN_ONLY."""
from __future__ import annotations

RULES: dict[str, dict] = {
    "SYN-S1-ADD_SYMBOL": {
        "design": "block_added",
        "code": "CREATE symbol (function/method template from design ports)",
        "supported": True,
        "finish": None,
    },
    "SYN-S2-MODIFY_SIGNATURE": {
        "design": "port_added/port_modified/port_removed/block_modified",
        "code": "replace target signature; update canonical callers",
        "supported": True,
        "finish": "sync known callers (coverage-aware)",
    },
    "SYN-S3-ADD_CALLSITE": {
        "design": "net_added (control/call)",
        "code": "INSERT real callsite in the bound caller body",
        "supported": True,
        "finish": None,
    },
    "SYN-S4-REMOVE_CALLSITE": {
        "design": "net_removed (control/call)",
        "code": "remove corresponding callsite",
        "supported": False,     # exact callsite removal via verifier
        "finish": None,
    },
    "SYN-S5-ADD_DATA_BINDING": {
        "design": "net_added (data)",
        "code": "create real value binding (argument/assignment/return)",
        "supported": True,
        "finish": None,
    },
    "SYN-S6-MODIFY_DATA_WIRING": {
        "design": "net_removed (data) / reroute",
        "code": "reroute data through the new intermediate",
        "supported": False,     # bounded cache pattern handled via S1+S5
        "finish": None,
    },
    "SYN-S7-RESOURCE_WIRING": {
        "design": "resource_added",
        "code": "constructor/parameter/import wiring",
        "supported": True,
        "finish": None,
    },
    "SYN-S8-COMPOSITE_BOUNDARY": {
        "design": "composite_added/composite_modified",
        "code": "expose boundary port wiring",
        "supported": False,
        "finish": None,
    },
}


def rule_for(synthesis_type: str) -> dict:
    return RULES.get(f"SYN-S{synthesis_type[1:]}-", {})


def supported(synthesis_type: str) -> bool:
    return bool(rule_for(synthesis_type).get("supported"))
