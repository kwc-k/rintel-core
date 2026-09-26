"""Local deployment tool surfaces; the MCP tools/list response is authoritative."""
from __future__ import annotations

READ_TOOLS = (
    "repo_status", "search_symbols", "get_symbol", "query_topology",
    "find_path", "explain_evidence", "query_flow", "query_runtime",
    "query_data_interface", "classify_evidence_authority", "read_resource",
)
DESIGN_EXECUTE_TOOLS = READ_TOOLS + (
    "create_design", "design_patch", "mutate_flow_design",
    "validate_design", "get_change_workspace",
    "get_git_collaboration", "get_agent_workspace", "request_agent_execution",
)
PRESETS = {"read": READ_TOOLS, "design-execute": DESIGN_EXECUTE_TOOLS}
