"""Bounded, single-TU runtime witness acquisition for RUNTIME-ALIGNMENT-EVIDENCE0.

This is an evidence harness, not a general profiler or a source of static
truth.  It fails closed unless binary offsets, DWARF source locations and
published canonical nodes form an exact, auditable bridge.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Mapping


PROVIDER = "bounded-appleclang-function-entry/1"
RELATION_SEMANTICS = "OBSERVED_CALL"
EVENT_FAMILY = "INSTRUMENTED_CALLER_CALLEE_ENTRY"
COMPILE_FLAGS = ("-std=c11", "-D_DARWIN_C_SOURCE", "-O0", "-g",
                 "-finstrument-functions", "-fno-inline")
_NM = re.compile(r"^([0-9a-fA-F]+) ([Tt]) (\S+)$")
_ATOS = re.compile(r"\(([^()]+):(\d+)\)$")


def digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv!r}: "
                           f"{result.stderr[-1200:]}")
    return result


def build_bounded_program(repo: Path, fixture: Path, *,
                          clang: str | None = None) -> dict[str, Any]:
    """Compile one copied C TU with exact recorded commands and no system writes."""
    repo.mkdir(parents=True, exist_ok=True)
    source = repo / "unit.c"
    shutil.copyfile(fixture, source)
    executable = repo / "unit"
    object_path = repo / "unit.o"
    # /usr/bin/clang on macOS is a launcher. Hash the real selected frontend,
    # not just the launcher whose target can change under xcode-select.
    selected = clang or _run(["xcrun", "--find", "clang"]).stdout.strip()
    compiler = Path(selected).resolve()
    if not compiler.is_file():
        raise RuntimeError("selected AppleClang executable unavailable")
    version = _run([str(compiler), "--version"]).stdout.splitlines()[0]
    target = _run([str(compiler), "-dumpmachine"]).stdout.strip()
    sdk = _run(["xcrun", "--show-sdk-path"]).stdout.strip()
    sdk_version = _run(["xcrun", "--show-sdk-version"]).stdout.strip()
    if not Path(sdk).is_dir():
        raise RuntimeError("selected macOS SDK unavailable")
    compiler_identity = {"executable": str(compiler), "version": version,
                         "target": target, "sha256": file_digest(compiler),
                         "sdk_path": sdk, "sdk_version": sdk_version}
    compile_argv = [str(compiler), "-isysroot", sdk, *COMPILE_FLAGS,
                    "-c", "unit.c", "-o", "unit.o"]
    link_argv = [str(compiler), "-isysroot", sdk, "-g", "unit.o", "-o", "unit"]
    _run(compile_argv, cwd=repo)
    _run(link_argv, cwd=repo)
    compdb = repo.parent / "compile_commands.json"
    compdb.write_text(json.dumps([{"directory": str(repo),
                                   "file": str(source),
                                   "arguments": compile_argv}], indent=2) + "\n")
    source_digest = file_digest(source)
    instrumentation = {"provider": PROVIDER, "event_family": EVENT_FAMILY,
                       "compile_flags": list(COMPILE_FLAGS),
                       "hook_source_digest": source_digest,
                       "trace_format": "E/X image-relative-hex-offset + END counts/1",
                       "scope": "single-process-single-thread-one-C-TU"}
    identity = digest({"compiler": compiler_identity,
                       "source_digest": source_digest,
                       "compile_argv": compile_argv, "link_argv": link_argv,
                       "instrumentation": instrumentation})
    return {"source": str(source), "source_revision": source_digest,
            "binary": str(executable), "binary_sha256": file_digest(executable),
            "object_sha256": file_digest(object_path),
            "compile_commands": str(compdb), "build_identity": identity,
            "compiler_identity": compiler_identity,
            "compile_command": compile_argv, "link_command": link_argv,
            "instrumentation": instrumentation,
            "instrumentation_identity": digest(instrumentation)}


def _symbol_locations(binary: Path) -> tuple[int, dict[int, dict[str, Any]]]:
    """Map *addresses* to DWARF paths/lines; symbol names are audit labels only."""
    symbols = []
    base = None
    for line in _run(["nm", "-n", str(binary)]).stdout.splitlines():
        match = _NM.fullmatch(line.strip())
        if not match:
            continue
        address, name = int(match[1], 16), match[3]
        if name == "__mh_execute_header":
            base = address
        else:
            symbols.append((address, name))
    if base is None or not symbols:
        raise RuntimeError("Mach-O text base or symbol table unavailable")
    addresses = [address for address, _ in symbols]
    output = _run(["atos", "-o", str(binary), "-l", hex(base),
                   *[hex(address) for address in addresses]]).stdout.splitlines()
    if len(output) != len(addresses):
        raise RuntimeError("DWARF address result count mismatch")
    locations: dict[int, dict[str, Any]] = {}
    for (address, name), line in zip(symbols, output, strict=True):
        match = _ATOS.search(line.strip())
        if not match:
            continue
        offset = address - base
        locations[offset] = {"vmaddr": hex(address), "image_offset": hex(offset),
                             "symbol_audit_label": name,
                             "debug_source": match[1],
                             "debug_line": int(match[2]),
                             "atos_output": line.strip()}
    return base, locations


def bridge_exact_endpoints(build: Mapping[str, Any], *, store: Any,
                           repo_id: str, canonical_revision: str,
                           node_ids: tuple[str, ...]) -> dict[str, Any]:
    """Bind a live binary offset to a canonical node through exact TU/line.

    No symbol spelling is used to select or merge an endpoint.  A missing or
    non-unique DWARF source span, or missing Clang admission support, fails.
    """
    binary = Path(build["binary"])
    if file_digest(binary) != build["binary_sha256"]:
        raise RuntimeError("binary changed before endpoint bridge")
    _base, locations = _symbol_locations(binary)
    source = Path(build["source"])
    if file_digest(source) != build["source_revision"]:
        raise RuntimeError("source changed before endpoint bridge")
    receipts: dict[str, dict[str, Any]] = {}
    used_offsets: set[int] = set()
    for node_id in node_ids:
        node = store.node_by_id(repo_id, canonical_revision, node_id)
        if not node or node.get("kind") != "FUNCTION" or node.get("path") != source.name:
            raise RuntimeError(f"canonical endpoint outside exact TU: {node_id}")
        support = [x for x in store.support_receipts(
            repo_id, canonical_revision, node_id)
            if x.get("lane_id") == "clang_provider"
            and x.get("canonical_revision") == canonical_revision
            and x.get("canonical_fact_type") == "node"]
        if not support:
            raise RuntimeError(f"endpoint lacks Clang admission support: {node_id}")
        first, last = node.get("start_line"), node.get("end_line")
        if not isinstance(first, int) or not isinstance(last, int):
            raise RuntimeError(f"endpoint lacks canonical source span: {node_id}")
        matches = [(offset, row) for offset, row in locations.items()
                   if Path(row["debug_source"]).name == source.name
                   and first <= row["debug_line"] <= last]
        if len(matches) != 1 or matches[0][0] in used_offsets:
            raise RuntimeError(f"non-unique binary/DWARF/canonical bridge: {node_id}")
        offset, location = matches[0]
        used_offsets.add(offset)
        payload = {"schema_version": "runtime-endpoint-bridge/1",
                   "binary_sha256": build["binary_sha256"],
                   "build_identity": build["build_identity"],
                   "source_revision": build["source_revision"],
                   "canonical_revision": canonical_revision,
                   "canonical_node_id": node_id,
                   "canonical_source_span": {"path": node["path"],
                                              "start_line": first,
                                              "end_line": last},
                   "canonical_support_receipts": sorted(
                       x["support_receipt_id"] for x in support),
                   **location}
        receipts[node_id] = {"id": "endpoint-" + digest(payload)[7:39],
                             **payload}
    return {"schema_version": "runtime-endpoint-bridge-set/1",
            "binary_sha256": build["binary_sha256"],
            "canonical_revision": canonical_revision,
            "endpoints": receipts}


def relation_bridge_id(bridge: Mapping[str, Any], source: str,
                       target: str) -> str:
    endpoints = bridge["endpoints"]
    return "bridge-" + digest({"source": endpoints[source]["id"],
                                "target": endpoints[target]["id"],
                                "binary_sha256": bridge["binary_sha256"],
                                "canonical_revision": bridge["canonical_revision"]})[7:39]


def decode_bounded_trace(path: Path, *, known_offsets: set[int]) -> dict[str, Any]:
    """Verify complete E/X stack and END marker; derive parent edges by nesting."""
    raw = path.read_text(encoding="ascii")
    lines = raw.splitlines()
    stack: list[int] = []
    entered: Counter[int] = Counter()
    edges: Counter[tuple[int, int]] = Counter()
    enter_count = exit_count = 0
    errors: list[str] = []
    if not lines or not lines[-1].startswith("END "):
        errors.append("end_marker_missing")
    for line in lines[:-1]:
        parts = line.split()
        if len(parts) != 3 or parts[0] not in {"E", "X"}:
            errors.append("malformed_event")
            continue
        try:
            offset = int(parts[1], 16)
            int(parts[2], 16)  # call-site offset retained in raw trace
        except ValueError:
            errors.append("malformed_offset")
            continue
        if offset not in known_offsets:
            errors.append("unmapped_function_offset")
        if parts[0] == "E":
            enter_count += 1
            entered[offset] += 1
            if stack:
                edges[(stack[-1], offset)] += 1
            stack.append(offset)
        else:
            exit_count += 1
            if not stack or stack.pop() != offset:
                errors.append("exit_stack_mismatch")
    if stack:
        errors.append("unclosed_frames")
    try:
        marker = lines[-1].split()
        if len(marker) != 4 or marker[0] != "END":
            raise ValueError
        marked_enter, marked_exit, failed = map(int, marker[1:])
        if (marked_enter, marked_exit) != (enter_count, exit_count):
            errors.append("event_count_mismatch")
        if failed:
            errors.append("trace_write_failure")
    except (ValueError, IndexError):
        errors.append("end_marker_malformed")
    if enter_count != exit_count:
        errors.append("enter_exit_count_mismatch")
    return {"status": "COMPLETE" if not errors else "PARTIAL",
            "errors": sorted(set(errors)), "enter": enter_count,
            "exit": exit_count, "event_count": enter_count + exit_count,
            "entered_offsets": {hex(k): v for k, v in entered.items()},
            "edges": [{"parent_offset": hex(a), "child_offset": hex(b),
                       "count": n} for (a, b), n in sorted(edges.items())],
            "trace_sha256": file_digest(path)}


def capture_bounded_run(build: Mapping[str, Any], bridge: Mapping[str, Any], *,
                        canonical_revision: str, source_id: str,
                        target_id: str, workload: str, output_dir: Path,
                        calibration: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Execute one of three fixed workloads and issue a scoped run receipt."""
    if workload not in {"direct-true", "direct-false", "indirect"}:
        raise ValueError("unsupported bounded workload")
    if bridge["binary_sha256"] != build["binary_sha256"] or bridge[
            "canonical_revision"] != canonical_revision:
        raise ValueError("bridge does not bind this binary/revision")
    endpoints = bridge["endpoints"]
    source_offset = int(endpoints[source_id]["image_offset"], 16)
    target_offset = int(endpoints[target_id]["image_offset"], 16)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_wall = datetime.now(timezone.utc).isoformat()
    start_ns = time.monotonic_ns()
    run_id = "run-rae0-" + workload + "-" + digest({
        "binary": build["binary_sha256"], "revision": canonical_revision,
        "source": source_id, "target": target_id, "workload": workload,
        "start_wall": start_wall, "start_monotonic_ns": start_ns})[7:19]
    trace = output_dir / f"{run_id}.trace"
    env = dict(os.environ)
    env["RINTEL_TRACE_FILE"] = str(trace)
    executed = subprocess.run([build["binary"], workload], env=env,
                              capture_output=True, text=True, check=False)
    end_ns = time.monotonic_ns()
    end_wall = datetime.now(timezone.utc).isoformat()
    if not trace.is_file():
        raise RuntimeError("bounded trace file absent")
    if file_digest(Path(build["binary"])) != build["binary_sha256"]:
        raise RuntimeError("binary changed during run")
    _base, locations = _symbol_locations(Path(build["binary"]))
    decoded = decode_bounded_trace(trace, known_offsets=set(locations))
    integrity_status = ("COMPLETE" if executed.returncode == 0 and
                        decoded["status"] == "COMPLETE" else "PARTIAL")
    calibration_id = None
    calibrated = False
    if calibration is not None:
        old = calibration.get("run", {})
        calibration_id = old.get("run_id")
        calibrated = (old.get("binary_sha256") == build["binary_sha256"]
                      and old.get("integrity", {}).get("status") == "COMPLETE"
                      and any(edge.get("parent_canonical") == source_id
                              and edge.get("child_canonical") == target_id
                              for edge in calibration.get("edges", ())))
    observed = any(item["parent_offset"] == hex(source_offset)
                   and item["child_offset"] == hex(target_offset)
                   for item in decoded["edges"])
    source_entered = decoded["entered_offsets"].get(hex(source_offset), 0) > 0
    # Absence needs a captured caller, a complete bounded process trace and
    # positive calibration proving the same target was instrumented in this
    # *same binary*. A failed calibration never creates NOT_OBSERVED.
    coverage_complete = (integrity_status == "COMPLETE" and source_entered
                         and (observed or calibrated))
    window = {"start": start_wall, "end": end_wall,
              "monotonic_start_ns": start_ns, "monotonic_end_ns": end_ns,
              "scope": "whole_single_process"}
    relation_bridge = relation_bridge_id(bridge, source_id, target_id)
    coverage_payload = {
        "schema_version": "runtime-coverage-receipt/1",
        "run_id": run_id, "binary_sha256": build["binary_sha256"],
        "build_identity": build["build_identity"],
        "instrumentation_identity": build["instrumentation_identity"],
        "canonical_revision": canonical_revision,
        "source_revision": build["source_revision"],
        "source": source_id, "target": target_id,
        "relation_kind": "CALLS", "event_family": EVENT_FAMILY,
        "relation_semantics": RELATION_SEMANTICS,
        "workload_id": workload, "trace_window": window,
        "trace_sha256": decoded["trace_sha256"],
        "trace_integrity": integrity_status,
        "source_entered": source_entered,
        "target_observed": observed,
        "same_binary_target_calibration_run": calibration_id if calibrated else None,
        "status": "COMPLETE" if coverage_complete else "PARTIAL",
        "limitations": ["single_process_single_thread", "instrumented_one_C_TU",
                        "bounded_workload_and_trace_window_only"],
    }
    coverage = {"id": "runtime-coverage-" + digest(coverage_payload)[7:39],
                **coverage_payload}
    offset_to_node = {int(item["image_offset"], 16): node_id
                      for node_id, item in endpoints.items()}
    edges = []
    for item in decoded["edges"]:
        parent = offset_to_node.get(int(item["parent_offset"], 16))
        child = offset_to_node.get(int(item["child_offset"], 16))
        if parent and child:
            edges.append({"parent_canonical": parent,
                          "child_canonical": child,
                          "parent_binding": "EXACT", "child_binding": "EXACT",
                          "parent_endpoint_receipt_id": endpoints[parent]["id"],
                          "child_endpoint_receipt_id": endpoints[child]["id"],
                          "relation_bridge_receipt_id": relation_bridge_id(
                              bridge, parent, child),
                          "parent_offset": item["parent_offset"],
                          "child_offset": item["child_offset"],
                          "count": item["count"]})
    run = {"run_id": run_id, "evidence_revision": canonical_revision,
           "repo_revision": build["source_revision"],
           "declared_input_digest": build["source_revision"],
           "source_path": build["source"],
           "binary": build["binary"],
           "binary_sha256": build["binary_sha256"],
           "build_identity": build["build_identity"],
           "build_command": {"compile": build["compile_command"],
                             "link": build["link_command"]},
           "compiler_identity": build["compiler_identity"],
           "instrumentation_identity": build["instrumentation_identity"],
           "instrumentation_config": build["instrumentation"],
           "trace_backend": PROVIDER, "provider_version": "1",
           "command": [build["binary"], workload],
           "environment_summary": {"RINTEL_TRACE_FILE": str(trace)},
           "workload_id": workload, "start_time": start_wall,
           "end_time": end_wall, "trace_window": window,
           "relation_semantics": RELATION_SEMANTICS,
           "scenario_binding": "EXACT",
           "bridge_receipt_id": relation_bridge,
           "exit_code": executed.returncode,
           "integrity": {"status": integrity_status,
                         "errors": decoded["errors"],
                         "enter": decoded["enter"], "exit": decoded["exit"],
                         "trace_sha256": decoded["trace_sha256"]},
           "relation_coverage": {"status": coverage["status"],
                                 "relation_kind": "CALLS",
                                 "source": source_id,
                                 "target": target_id,
                                 "receipt_id": coverage["id"]}}
    artifact = {"run": run, "edges": edges,
                "endpoint_bridge": {source_id: endpoints[source_id],
                                    target_id: endpoints[target_id]},
                "runtime_coverage_receipt": coverage,
                "trace": {"path": str(trace), **decoded},
                "stdout": executed.stdout, "stderr": executed.stderr}
    (output_dir / f"{run_id}.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return artifact


def validate_bounded_artifact(artifact: Mapping[str, Any], *,
                              check_calibration: bool = True
                              ) -> tuple[bool, str]:
    """Recheck a bounded run from its retained binary, source and raw trace.

    The JSON COMPLETE label alone is not authority.  This is intentionally
    narrow to the local one-TU witness provider, not a generic trace importer.
    """
    try:
        run = artifact["run"]
        coverage = artifact["runtime_coverage_receipt"]
        bridge = artifact["endpoint_bridge"]
        trace = artifact["trace"]
        if run["trace_backend"] != PROVIDER or coverage[
                "schema_version"] != "runtime-coverage-receipt/1":
            return False, "unsupported_bounded_provider_or_receipt"
        if coverage["id"] != "runtime-coverage-" + digest({
                k: v for k, v in coverage.items() if k != "id"})[7:39]:
            return False, "runtime_coverage_receipt_digest_mismatch"
        if (file_digest(Path(run["binary"])) != run["binary_sha256"] or
                file_digest(Path(run["source_path"])) != run["repo_revision"]):
            return False, "source_or_binary_digest_mismatch"
        if (run["declared_input_digest"] != run["repo_revision"] or
                digest(run["instrumentation_config"]) != run[
                    "instrumentation_identity"] or
                digest({"compiler": run["compiler_identity"],
                        "source_digest": run["repo_revision"],
                        "compile_argv": run["build_command"]["compile"],
                        "link_argv": run["build_command"]["link"],
                        "instrumentation": run["instrumentation_config"]}) !=
                run["build_identity"] or
                run["command"] != [run["binary"], run["workload_id"]] or
                run["environment_summary"].get("RINTEL_TRACE_FILE") !=
                trace["path"]):
            return False, "run_command_or_instrumentation_mismatch"
        if any(coverage[key] != run[run_key] for key, run_key in (
                ("run_id", "run_id"), ("binary_sha256", "binary_sha256"),
                ("build_identity", "build_identity"),
                ("instrumentation_identity", "instrumentation_identity"),
                ("canonical_revision", "evidence_revision"),
                ("source_revision", "repo_revision"),
                ("relation_semantics", "relation_semantics"),
                ("workload_id", "workload_id"),
                ("trace_window", "trace_window"))):
            return False, "run_and_coverage_identity_mismatch"
        if (run["relation_coverage"]["receipt_id"] != coverage["id"] or
                run["relation_coverage"]["status"] != coverage["status"] or
                run["relation_coverage"]["source"] != coverage["source"] or
                run["relation_coverage"]["target"] != coverage["target"]):
            return False, "relation_coverage_receipt_mismatch"
        source, target = coverage["source"], coverage["target"]
        if set(bridge) != {source, target}:
            return False, "endpoint_bridge_domain_mismatch"
        for node_id, receipt in bridge.items():
            if receipt["canonical_node_id"] != node_id or receipt[
                    "id"] != "endpoint-" + digest({
                        k: v for k, v in receipt.items() if k != "id"})[7:39]:
                return False, "endpoint_bridge_receipt_mismatch"
            if (receipt["binary_sha256"] != run["binary_sha256"] or
                    receipt["build_identity"] != run["build_identity"] or
                    receipt["source_revision"] != run["repo_revision"] or
                    receipt["canonical_revision"] != run["evidence_revision"]):
                return False, "endpoint_bridge_identity_mismatch"
        expected_bridge = "bridge-" + digest({
            "source": bridge[source]["id"], "target": bridge[target]["id"],
            "binary_sha256": run["binary_sha256"],
            "canonical_revision": run["evidence_revision"]})[7:39]
        if expected_bridge != run["bridge_receipt_id"]:
            return False, "relation_bridge_identity_mismatch"
        trace_path = Path(trace["path"])
        if file_digest(trace_path) != coverage["trace_sha256"]:
            return False, "raw_trace_digest_mismatch"
        _base, locations = _symbol_locations(Path(run["binary"]))
        decoded = decode_bounded_trace(trace_path,
                                       known_offsets=set(locations))
        integrity = ("COMPLETE" if run["exit_code"] == 0 and
                     decoded["status"] == "COMPLETE" else "PARTIAL")
        if (integrity != run["integrity"]["status"] or
                integrity != coverage["trace_integrity"] or
                decoded["trace_sha256"] != run["integrity"]["trace_sha256"] or
                decoded["enter"] != run["integrity"]["enter"] or
                decoded["exit"] != run["integrity"]["exit"]):
            return False, "trace_integrity_receipt_mismatch"
        offsets = {int(item["image_offset"], 16): node_id
                   for node_id, item in bridge.items()}
        expected_edges = Counter()
        for edge in decoded["edges"]:
            parent = int(edge["parent_offset"], 16)
            child = int(edge["child_offset"], 16)
            if parent in offsets and child in offsets:
                expected_edges[(parent, child)] += edge["count"]
        actual_edges = Counter()
        for edge in artifact["edges"]:
            parent = int(edge["parent_offset"], 16)
            child = int(edge["child_offset"], 16)
            if (edge["parent_binding"] != "EXACT" or
                    edge["child_binding"] != "EXACT" or
                    offsets.get(parent) != edge["parent_canonical"] or
                    offsets.get(child) != edge["child_canonical"] or
                    edge["parent_endpoint_receipt_id"] != bridge[
                        edge["parent_canonical"]]["id"] or
                    edge["child_endpoint_receipt_id"] != bridge[
                        edge["child_canonical"]]["id"] or
                    edge["relation_bridge_receipt_id"] != "bridge-" + digest({
                        "source": bridge[edge["parent_canonical"]]["id"],
                        "target": bridge[edge["child_canonical"]]["id"],
                        "binary_sha256": run["binary_sha256"],
                        "canonical_revision": run["evidence_revision"]})[7:39]):
                return False, "edge_endpoint_bridge_mismatch"
            actual_edges[(parent, child)] += edge["count"]
        if actual_edges != expected_edges:
            return False, "edge_rows_differ_from_raw_trace"
        source_offset = int(bridge[source]["image_offset"], 16)
        target_offset = int(bridge[target]["image_offset"], 16)
        observed = expected_edges[(source_offset, target_offset)] > 0
        source_entered = decoded["entered_offsets"].get(
            hex(source_offset), 0) > 0
        if (coverage["target_observed"] != observed or
                coverage["source_entered"] != source_entered):
            return False, "coverage_observation_mismatch"
        calibrated = False
        calibration_id = coverage["same_binary_target_calibration_run"]
        if calibration_id and check_calibration:
            calibration_path = trace_path.parent / f"{calibration_id}.json"
            calibration_artifact = json.loads(calibration_path.read_text())
            valid, reason = validate_bounded_artifact(
                calibration_artifact, check_calibration=False)
            if not valid:
                return False, f"calibration_invalid:{reason}"
            calibration_run = calibration_artifact["run"]
            calibrated = (calibration_run["run_id"] == calibration_id and
                          calibration_run["binary_sha256"] == run["binary_sha256"]
                          and calibration_run["evidence_revision"] == run[
                              "evidence_revision"]
                          and calibration_run["bridge_receipt_id"] ==
                          expected_bridge and any(
                              edge["parent_canonical"] == source and
                              edge["child_canonical"] == target
                              for edge in calibration_artifact["edges"]))
            if not calibrated:
                return False, "calibration_does_not_prove_same_binary_target"
        expected_complete = (integrity == "COMPLETE" and source_entered
                             and (observed or calibrated))
        if (coverage["status"] == "COMPLETE") != expected_complete:
            return False, "runtime_coverage_completeness_mismatch"
        return True, "bounded_runtime_artifact_verified"
    except (KeyError, TypeError, ValueError, OSError, RuntimeError,
            json.JSONDecodeError):
        return False, "bounded_runtime_artifact_unavailable_or_malformed"


__all__ = ["PROVIDER", "RELATION_SEMANTICS", "EVENT_FAMILY",
           "build_bounded_program", "bridge_exact_endpoints",
           "relation_bridge_id", "decode_bounded_trace", "capture_bounded_run",
           "validate_bounded_artifact", "digest", "file_digest"]
