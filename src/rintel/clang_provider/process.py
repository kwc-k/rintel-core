"""External rpp/1 lifecycle for the Clang Provider.

This module deliberately imports no Rintel canonical, database, or publication
module. Its authority ends at provider-local candidate facts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

from .compile_context import load_translation_unit
from .extractor import extract_facts, extract_include_facts
from .frontend import ClangFrontend, dependency_paths, detect_clang_identity
from .incremental import IncrementalAnalyzer, semantic_fact_digest
from .model import digest


PROTOCOL_VERSION = "rpp/1"
PROVIDER_ID = "rintel-clang"
PROVIDER_VERSION = "provider-clang0.1"
PROVIDER_CONFIG_DIGEST = digest({
    "provider": PROVIDER_ID,
    "version": PROVIDER_VERSION,
    "frontend": "clang-json-ast+sarif",
})


def _read(stream: TextIO) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise RuntimeError("Host stream closed")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("RPP message must be an object")
    return value


def _send(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    stream.flush()


def _advertisement() -> dict[str, Any]:
    return {
        "provider_id": PROVIDER_ID,
        "provider_version": PROVIDER_VERSION,
        "provider_kind": "SOURCE_FRONTEND",
        "provider_config_digest": PROVIDER_CONFIG_DIGEST,
        "languages": ["c"],
        "capabilities": [
            "symbols", "references", "calls", "bindings", "includes"],
        "incremental_capability": {
            "modes": [
                "NO_CHANGE", "FILE_LOCAL", "DEPENDENCY_AWARE",
                "SEMANTIC_CUTOFF"],
            "accepts_changed_scope": True,
            "reports_invalidated_scope": True,
            "reports_dependency_manifest": True,
            "reports_semantic_digest": True,
            "reports_reuse_source": True,
        },
    }


def _rows(contract: dict[str, Any], *, incremental: bool = False
          ) -> list[dict[str, Any]]:
    config = contract["provider_config"]
    path = Path(config["compile_commands"])
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError("compile_commands must be an array of objects")
    requested = (set() if incremental else set(
        contract["invalidated_scope"] or contract["changed_scope"]))
    root = Path(contract["root"]).resolve()
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    for row in value:
        source = Path(str(row["file"]))
        if not source.is_absolute():
            source = Path(str(row["directory"])) / source
        try:
            relative = source.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if not requested or relative in requested:
            selected.append(row)
            covered.add(relative)
    if requested:
        missing = {
            item for item in requested - covered
            if Path(item).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}
        }
        if missing:
            raise RuntimeError(
                "requested scope lacks compile command: " + sorted(missing)[0])
    return selected


def _analyze(contract: dict[str, Any]) -> tuple[
        list[dict[str, Any]], str, dict[str, Any], tuple[Any, Any] | None]:
    config = contract["provider_config"]
    clang = detect_clang_identity(config.get("clang_executable", "clang"))
    root = Path(contract["root"])
    cache_root = config.get("cache_root")
    rows = _rows(contract, incremental=bool(cache_root))
    if cache_root:
        analyzer = IncrementalAnalyzer(
            repo_root=root,
            compile_commands=rows,
            clang=clang,
            cache_root=cache_root,
            provider_version=PROVIDER_VERSION,
            semantic_schema_version=(
                contract["semantic_identity_schema_version"]),
            max_output_bytes=int(config.get(
                "max_output_bytes", 768 * 1024 * 1024)),
        )
        result = analyzer.run(publish=False)
        cutoff = {
            "INPUT_IDENTITY_UNCHANGED": "NO_CHANGE",
            "SEMANTIC_DIGEST_UNCHANGED": "SEMANTIC_UNCHANGED",
            "NONE": "NONE",
        }[result.cutoff_reason]
        return list(result.facts), result.result_identity, {
            "cutoff_reason": cutoff,
            "reuse_source": (str(Path(cache_root) / "current.json")
                             if cutoff != "NONE" else None),
            "invalidated_scope": list(result.tus_reparsed),
        }, (analyzer, result)
    facts: list[dict[str, Any]] = []
    frontend = ClangFrontend(max_output_bytes=int(config.get(
        "max_output_bytes", 768 * 1024 * 1024)))
    for row in rows:
        unit = load_translation_unit(
            row, clang, provider_version=PROVIDER_VERSION,
            semantic_schema_version=contract["semantic_identity_schema_version"],
        )
        frontend_result = frontend.analyze(unit)
        facts.extend(extract_facts(frontend_result.ast, unit, repo_root=root))
        facts.extend(extract_include_facts(
            unit, dependency_paths(unit), repo_root=root))
    facts.sort(key=lambda item: item["provider_fact_id"])
    return facts, semantic_fact_digest(facts), {
        "cutoff_reason": "NONE",
        "reuse_source": None,
        "invalidated_scope": list(contract["invalidated_scope"]),
    }, None


def _observed_capabilities(facts: list[dict[str, Any]]) -> list[str]:
    kinds = {fact["kind"] for fact in facts}
    mapping = (
        ("symbols", {"Function", "Declaration"}),
        ("references", {"Reference"}),
        ("calls", {"CALL", "IndirectCall"}),
        ("bindings", {"Binding"}),
        ("includes", {"Include"}),
    )
    return [capability for capability, fact_kinds in mapping if kinds & fact_kinds]


def run(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    hello = _read(stdin)
    if hello.get("protocol_version") != PROTOCOL_VERSION or hello.get("type") != "hello":
        return 3
    _send(stdout, {
        "protocol_version": PROTOCOL_VERSION,
        "type": "hello_ack",
        "process_id": os.getpid(),
        "advertisement": _advertisement(),
    })
    analyze = _read(stdin)
    if analyze.get("protocol_version") != PROTOCOL_VERSION \
            or analyze.get("type") != "analyze":
        return 4
    contract = analyze["contract"]
    analysis_id = contract["analysis_id"]
    _send(stdout, {
        "protocol_version": PROTOCOL_VERSION,
        "type": "analysis_started",
        "analysis_id": analysis_id,
    })
    facts, semantic_digest, incremental, cache_commit = _analyze(contract)
    batches = [facts[index:index + 500] for index in range(0, len(facts), 500)]
    for sequence, batch in enumerate(batches):
        _send(stdout, {
            "protocol_version": PROTOCOL_VERSION,
            "type": "fact_batch",
            "analysis_id": analysis_id,
            "sequence": sequence,
            "facts": batch,
        })
    _send(stdout, {
        "protocol_version": PROTOCOL_VERSION,
        "type": "analysis_complete",
        "analysis_id": analysis_id,
        "status": "COMPLETE",
        "batch_count": len(batches),
        "fact_count": len(facts),
        "coverage": {
            "level": "COMPLETE",
            "scope": contract["invalidated_scope"],
            "capabilities_observed": _observed_capabilities(facts),
            "limitations": [],
        },
        "incremental_result": {
            "semantic_digest": semantic_digest,
            "semantic_identity_schema_version": contract["semantic_identity_schema_version"],
            "invalidated_scope": incremental["invalidated_scope"],
            "dependency_manifest_digest": contract["dependency_manifest_digest"],
            "cache_identity": contract["cache_identity"],
            "cutoff_reason": incremental["cutoff_reason"],
            "reuse_source": incremental["reuse_source"],
            "projection_identity": contract["projection_identity"],
            "projection_version": contract["projection_version"],
        },
    })
    shutdown = _read(stdin)
    if shutdown.get("type") != "shutdown":
        return 5
    if cache_commit is not None:
        analyzer, incremental_run = cache_commit
        analyzer.publish(incremental_run)
    _send(stdout, {"protocol_version": PROTOCOL_VERSION, "type": "shutdown_ack"})
    return 0


__all__ = ["run"]
