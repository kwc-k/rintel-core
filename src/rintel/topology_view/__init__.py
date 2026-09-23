"""TOPO-UI0 renderer-neutral Software Topology view model.

Consumes ONLY frozen upstream artifacts (TOPO-ENGINE0 / MODULE-INFER0 /
MODULE-OPT0 outputs under analysis_tournament/).  No re-analysis, no
canonical-topology mutation.  The server is a thin read-only reader; the
projection rules mirror upstream semantics exactly (e.g. declared module =
first path component of the node's file, as in module_infer TopologyArtifact).
"""
