"""Conservative AST audit for the single C direct-static-call domain."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from rintel.provider_arch import EvidenceCandidate


def _walk(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in node.get("inner", ()):
        if isinstance(child, Mapping):
            yield from _walk(child)


def audit_direct_body(
        ast: Mapping[str, Any], diagnostics: Mapping[str, Any], *,
        source: Path, subject_id: str, candidates: tuple[EvidenceCandidate, ...],
        existing_edges: set[str]) -> tuple[bool, list[str], Counter[str]]:
    """Prove all CallExpr in one global C body are known direct calls.

    The provider's CALL facts are independently compared with the AST callee
    expression, never trusted as a coverage declaration by themselves.
    Unsupported call shapes make the entire requested body PARTIAL.
    """
    reasons: list[str] = []
    for run in diagnostics.get("runs", ()):
        if not isinstance(run, Mapping):
            reasons.append("malformed_diagnostic_run")
            continue
        for result in run.get("results", ()):
            if not isinstance(result, Mapping) or result.get("level") in {
                    "error", "fatal"}:
                reasons.append("clang_error_diagnostic")
    name = subject_id.removeprefix("node:FUNCTION:")
    if not name or name == subject_id or "::" in name:
        reasons.append("subject_not_global_c_function")
    bodies: list[Mapping[str, Any]] = []
    for node in _walk(ast):
        if node.get("kind") != "FunctionDecl" or node.get("name") != name:
            continue
        if node.get("storageClass") == "static":
            reasons.append("static_subject_outside_first_issuer")
            continue
        location = node.get("loc")
        if not isinstance(location, Mapping) or "includedFrom" in location:
            continue
        file_value = location.get("file")
        if isinstance(file_value, str):
            candidate = Path(file_value)
            if not candidate.is_absolute():
                candidate = source.parent / candidate
            if candidate.resolve() != source.resolve():
                continue
        elif not isinstance(location.get("offset"), int):
            continue
        compounds = [item for item in node.get("inner", ())
                     if isinstance(item, Mapping)
                     and item.get("kind") == "CompoundStmt"]
        bodies.extend(compounds)
    if len(bodies) != 1:
        reasons.append("subject_body_not_unique_in_exact_tu")
        return False, sorted(set(reasons)), Counter()

    direct: Counter[str] = Counter()
    for node in _walk(bodies[0]):
        kind = node.get("kind")
        if kind in {"CXXMemberCallExpr", "CXXOperatorCallExpr", "CUDAKernelCallExpr"}:
            reasons.append("unsupported_call_expression_kind")
        if kind != "CallExpr":
            continue
        source_range = node.get("range")
        begin = source_range.get("begin") if isinstance(source_range, Mapping) else None
        if not isinstance(begin, Mapping) or "expansionLoc" in begin:
            reasons.append("macro_or_missing_call_location")
            continue
        inner = node.get("inner")
        if not isinstance(inner, list) or not inner or not isinstance(inner[0], Mapping):
            reasons.append("call_callee_shape_unknown")
            continue
        # Search only the callee expression. A FunctionDecl passed as an
        # *argument* to an indirect call must not turn it into a direct call.
        references = [item.get("referencedDecl") for item in _walk(inner[0])
                      if item.get("kind") == "DeclRefExpr"]
        if len(references) != 1 or not isinstance(references[0], Mapping):
            reasons.append("indirect_or_ambiguous_callee")
            continue
        ref = references[0]
        if ref.get("kind") != "FunctionDecl" or not isinstance(ref.get("name"), str):
            reasons.append("indirect_or_ambiguous_callee")
            continue
        target = f"node:FUNCTION:{ref['name']}"
        direct[target] += 1

    staged = Counter(str(item.object) for item in candidates
                     if item.subject == subject_id and item.predicate == "CALL"
                     and item.resolution.value == "EXACT")
    if staged != direct:
        reasons.append("ast_direct_calls_differ_from_rpp_candidates")
    if any(f"edge:CALLS:{subject_id}:{target}" not in existing_edges
           for target in direct):
        reasons.append("compiler_direct_call_absent_from_canonical_graph")
    return not reasons, sorted(set(reasons)), direct


__all__ = ["audit_direct_body"]
