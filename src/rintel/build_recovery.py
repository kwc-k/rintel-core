"""Bounded, evidence-preserving build observations for BUILD-RECOVERY0.

The runner accepts only host-registered profiles. It never edits source, design,
canonical evidence, or test receipts. Build success is not test verification.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .execution_authority import (
    ExecutionAuthority, ExecutionProfile, ExecutionRequest,
)


class BuildRecoveryError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


@dataclass(frozen=True)
class BuildProfile:
    """Installed by the host, never selected by a producer-supplied command."""

    id: str
    repo_id: str
    cwd: str
    command: tuple[str, ...]
    source_files: tuple[str, ...]
    compiler: str
    defines: tuple[str, ...] = ()
    include_paths: tuple[str, ...] = ()
    module_paths: tuple[str, ...] = ()
    libraries: tuple[str, ...] = ()
    target: str = ""
    timeout_seconds: int = 120
    version: str = "1"
    stdout_limit_bytes: int = 1024 * 1024
    stderr_limit_bytes: int = 1024 * 1024
    artifact_files: tuple[str, ...] = ()
    artifact_limit_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.id or not self.repo_id or not self.command or not self.source_files:
            raise BuildRecoveryError("profile requires id, repo, command and source files")
        if not Path(self.cwd).is_absolute() or not Path(self.command[0]).is_absolute():
            raise BuildRecoveryError("cwd and executable must be absolute")
        if Path(self.compiler).resolve() != Path(self.command[0]).resolve():
            raise BuildRecoveryError("declared compiler does not match executable")
        if not 1 <= self.timeout_seconds <= 600:
            raise BuildRecoveryError("timeout out of bounded range")

    def execution_profile(self) -> ExecutionProfile:
        return ExecutionProfile(
            id=self.id, version=self.version, kind="BUILD", repo_id=self.repo_id,
            cwd=self.cwd, allowed_root=self.cwd, argv=tuple(self.command),
            input_files=tuple(self.source_files), timeout_seconds=self.timeout_seconds,
            stdout_limit_bytes=self.stdout_limit_bytes,
            stderr_limit_bytes=self.stderr_limit_bytes,
            artifact_files=tuple(self.artifact_files),
            artifact_limit_bytes=self.artifact_limit_bytes)


_LOCATION = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
                       r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.*)$",
                       re.IGNORECASE)
_FORTRAN_LOCATION = re.compile(r"^(?:Fatal )?Error:\s*(?P<message>.*)$",
                               re.IGNORECASE)


def _category(message: str, raw: str) -> str:
    lower = (message + " " + raw).lower()
    if "no such file or directory" in lower or "file not found" in lower:
        return "MISSING_INCLUDE_OR_MODULE"
    if "cannot open module file" in lower or "module file" in lower and "not found" in lower:
        return "MISSING_INCLUDE_OR_MODULE"
    if "undefined reference" in lower or "undefined symbols" in lower or "symbol(s) not found" in lower:
        return "UNDEFINED_SYMBOL"
    if "duplicate symbol" in lower or "multiple definition" in lower:
        return "DUPLICATE_SYMBOL"
    if any(term in lower for term in ("incompatible type", "conflicting types", "type mismatch",
                                      "argument mismatch", "rank mismatch")):
        return "TYPE_OR_INTERFACE_MISMATCH"
    if any(term in lower for term in ("expected", "syntax error", "unexpected")):
        return "SYNTAX_ERROR"
    return "COMPILE_OR_LINK_ERROR"


def normalize_diagnostics(raw: str, *, attempt_id: str, tool: str,
                          raw_ref: str) -> list[dict[str, Any]]:
    """Conservative adapter: retains raw, never fabricates a compiler code."""
    out: list[dict[str, Any]] = []
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        match = _LOCATION.match(line)
        if match:
            row = match.groupdict()
            message = row["message"]
            span = {"path": row["file"], "start_line": int(row["line"]),
                    "start_column": int(row["column"]) if row["column"] else None}
            severity = "ERROR" if "error" in row["severity"] else row["severity"].upper()
        elif ("undefined reference" in line.lower() or "duplicate symbol" in line.lower()
              or "multiple definition" in line.lower() or "undefined symbols" in line.lower()):
            message, span, severity = line.strip(), None, "ERROR"
        elif _FORTRAN_LOCATION.match(line) and index > 0:
            message = _FORTRAN_LOCATION.match(line).group("message")
            span, severity = None, "ERROR"
            previous = "\n".join(lines[max(0, index - 8):index])
            fm = re.search(r"([^\s:]+):(?P<line>\d+):(?P<column>\d+):", previous)
            if fm:
                span = {"path": fm.group(1), "start_line": int(fm.group("line")),
                        "start_column": int(fm.group("column"))}
        else:
            continue
        out.append({"id": f"{attempt_id}:diagnostic:{len(out) + 1}",
                    "attempt_id": attempt_id, "tool": tool, "severity": severity,
                    "diagnostic_code": None, "category": _category(message, line),
                    "message": message, "source_span": span,
                    "related_locations": [], "raw_ref": raw_ref,
                    "raw_line": index + 1, "authority": "OBSERVED",
                    "category_authority": "RINTEL_INFERRED"})
    if not out and raw.strip():
        out.append({"id": f"{attempt_id}:diagnostic:1", "attempt_id": attempt_id,
                    "tool": tool, "severity": "UNKNOWN", "diagnostic_code": None,
                    "category": "UNCLASSIFIED", "message": raw.splitlines()[-1][:500],
                    "source_span": None, "related_locations": [], "raw_ref": raw_ref,
                    "raw_line": len(lines), "authority": "OBSERVED",
                    "category_authority": "RINTEL_INFERRED"})
    return out


def _source_identity(profile: BuildProfile) -> dict[str, str]:
    root = Path(profile.cwd).resolve()
    identity: dict[str, str] = {}
    for source in profile.source_files:
        path = (root / source).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise BuildRecoveryError(f"source unavailable or outside cwd: {source}")
        identity[source] = _sha(path.read_bytes())
    return identity


class BuildRecoveryService:
    def __init__(self, root: str | Path, profiles: tuple[BuildProfile, ...],
                 *, change_lookup: Callable[[str], Any] | None = None,
                 application_recorder: Callable[[str, Any], Any] | None = None,
                 execution_authority: ExecutionAuthority | None = None):
        self.root = Path(root).resolve()
        self.profiles = {profile.id: profile for profile in profiles}
        self.change_lookup = change_lookup
        self.application_recorder = application_recorder
        self.execution_authority = execution_authority or ExecutionAuthority(
            self.root / "execution", tuple(p.execution_profile() for p in profiles))

    def _read(self, kind: str, identity: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z]+-[0-9a-f]{32}", identity):
            raise BuildRecoveryError("invalid identity")
        path = self.root / kind / identity / "record.json"
        if not path.is_file():
            raise BuildRecoveryError(f"{kind} not found")
        return json.loads(path.read_text())

    def _publish(self, kind: str, identity: str, record: dict[str, Any],
                 blobs: dict[str, bytes] | None = None) -> None:
        target = self.root / kind / identity
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.parent / f".pending-{uuid.uuid4().hex}"
        pending.mkdir()
        try:
            (pending / "record.json").write_bytes(_json(record))
            for name, blob in (blobs or {}).items():
                (pending / name).write_bytes(blob)
            pending.rename(target)
        except Exception:
            shutil.rmtree(pending)
            raise

    def _change(self, change_id: str | None, profile: BuildProfile) -> Any:
        if not change_id:
            return None
        if not self.change_lookup:
            raise BuildRecoveryError("DesignChange lookup unavailable")
        change = self.change_lookup(change_id)
        if change.repo_id != profile.repo_id:
            raise BuildRecoveryError("DesignChange repo mismatch")
        if change.state.value in {"CLOSED", "ABANDONED"}:
            raise BuildRecoveryError("terminal DesignChange cannot accept new build activity")
        return change

    def run(self, profile_id: str, *, change_id: str | None = None,
            previous_attempt: str | None = None,
            application_id: str | None = None) -> dict[str, Any]:
        profile = self.profiles.get(profile_id)
        if not profile:
            raise BuildRecoveryError("unknown host build profile")
        change = self._change(change_id, profile)
        previous = self.get_attempt(previous_attempt) if previous_attempt else None
        application = self._read("applications", application_id) if application_id else None
        if (previous and (previous["repo_id"] != profile.repo_id
                          or previous["change_id"] != change_id)):
            raise BuildRecoveryError("previous attempt binding mismatch")
        if application and (not previous or application["previous_attempt"] != previous_attempt
                            or application["change_id"] != change_id):
            raise BuildRecoveryError("application lineage mismatch")
        if previous and not application:
            raise BuildRecoveryError("rebuild requires an application receipt")
        source = _source_identity(profile)
        if application and _sha(_json(source)) != application["source_revision_after_claim"]:
            raise BuildRecoveryError("source changed after application receipt; record a new claim")
        expected = self.execution_authority.source_revision(profile_id)
        execution = self.execution_authority.execute(ExecutionRequest(
            profile_id=profile_id, kind="BUILD", change_id=change_id,
            canonical_revision=(change.implementation or {}).get("current_evidence_revision")
            if change else None,
            design_revision=change.design_revision.id if change else None,
            expected_source_revision=expected,
            previous_execution=previous.get("execution_id") if previous else None,
            lineage_ref=application_id))
        stdout = self.execution_authority.raw_output(execution["execution_id"], "stdout")
        stderr = self.execution_authority.raw_output(execution["execution_id"], "stderr")
        status = execution["exit_code"]
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        tool = Path(execution["tool_identity"]["path"]).name
        raw = stderr.decode(errors="replace")
        diagnostics = normalize_diagnostics(raw, attempt_id=attempt_id, tool=tool,
                                            raw_ref=f"attempts/{attempt_id}/stderr.bin")
        before_failed = previous is not None and previous["verification_status"] not in {
            "BUILD_PASS_ONLY", "BUILD_FIXED_BUT_UNVERIFIED",
            "BUILD_FIXED_BUT_TEST_AUTHORITY_UNAVAILABLE"}
        if execution["termination_reason"] == "TIMEOUT":
            verification = "BUILD_TIMEOUT"
        elif execution["termination_reason"] == "INPUT_DRIFT":
            verification = "BUILD_INPUT_DRIFT"
        elif execution["result"] not in {"PASS", "FAIL"}:
            verification = "BUILD_EXECUTION_ERROR"
        elif execution["result"] == "FAIL":
            verification = "REGRESSION" if previous and previous["exit_status"] == 0 else "BUILD_FAILED"
        elif before_failed:
            verification = "BUILD_FIXED_BUT_UNVERIFIED"
        else:
            verification = "BUILD_PASS_ONLY"
        record = {
            "schema_version": "build-attempt/1", "attempt_id": attempt_id,
            "profile_id": profile.id, "repo_id": profile.repo_id,
            "change_id": change_id, "source_revision": execution["source_revision"],
            "source_files": source,
            "canonical_revision": execution["canonical_revision"],
            "design_revision": execution["design_revision"],
            "cwd": execution["cwd"], "command": execution["resolved_argv"],
            "compiler_identity": execution["tool_identity"],
            "environment_identity": execution["environment_identity"],
            "environment": execution["selected_environment"],
            "defines": list(profile.defines), "include_paths": list(profile.include_paths),
            "module_paths": list(profile.module_paths), "libraries": list(profile.libraries),
            "target": profile.target,
            "started_ns": execution["started_ns"], "ended_ns": execution["finished_ns"],
            "exit_status": status,
            "timed_out": execution["termination_reason"] == "TIMEOUT",
            "stdout_ref": f"attempts/{attempt_id}/stdout.bin",
            "stderr_ref": f"attempts/{attempt_id}/stderr.bin",
            "stdout_sha256": _sha(stdout), "stderr_sha256": _sha(stderr),
            "diagnostics": diagnostics, "previous_attempt": previous_attempt,
            "application_id": application_id, "verification_status": verification,
            "execution_id": execution["execution_id"],
            "execution_receipt_ref": f"executions/{execution['execution_id']}/receipt.json",
            "execution_result": execution["result"],
            "termination_reason": execution["termination_reason"],
            "stdout_truncated": execution["stdout_truncated"],
            "stderr_truncated": execution["stderr_truncated"],
            "input_closure": execution["input_closure"],
            "test_authority": "NOT_RUN", "design_verification": "UNAVAILABLE",
            "authority": "OBSERVED",
        }
        self._publish("attempts", attempt_id, record,
                      {"stdout.bin": stdout, "stderr.bin": stderr})
        return record

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        return self._read("attempts", attempt_id)

    def list_attempts(self, *, change_id: str | None = None) -> list[dict[str, Any]]:
        directory = self.root / "attempts"
        if not directory.exists():
            return []
        rows = [json.loads(path.read_text()) for path in directory.glob("attempt-*/record.json")]
        if change_id is not None:
            rows = [row for row in rows if row["change_id"] == change_id]
        return sorted(rows, key=lambda row: row["started_ns"], reverse=True)

    def raw_output(self, attempt_id: str, stream: str) -> bytes:
        self.get_attempt(attempt_id)
        if stream not in {"stdout", "stderr"}:
            raise BuildRecoveryError("invalid stream")
        return (self.root / "attempts" / attempt_id / f"{stream}.bin").read_bytes()

    def propose_repairs(self, attempt_id: str) -> list[dict[str, Any]]:
        attempt = self.get_attempt(attempt_id)
        kinds = {"MISSING_INCLUDE_OR_MODULE": "CHANGE_INCLUDE_OR_MODULE_PATH",
                 "UNDEFINED_SYMBOL": "CHANGE_LINKAGE",
                 "DUPLICATE_SYMBOL": "CHANGE_DECLARATION_OR_LINKAGE",
                 "TYPE_OR_INTERFACE_MISMATCH": "CHANGE_DECLARATION_OR_INTERFACE",
                 "SYNTAX_ERROR": "EDIT_SOURCE"}
        return [{"id": f"{d['id']}:repair", "attempt_id": attempt_id,
                 "diagnostic_id": d["id"], "kind": kinds.get(d["category"], "INSPECT_SOURCE_OR_BUILD"),
                 "description": "Review compiler diagnostic and source; no patch is generated",
                 "authority": "DESIGN_ANNOTATION", "status": "PROPOSED"}
                for d in attempt["diagnostics"]]

    def record_application(self, *, previous_attempt: str, repair_candidate_id: str,
                           change_id: str, actor: str) -> dict[str, Any]:
        previous = self.get_attempt(previous_attempt)
        if previous["change_id"] != change_id or not change_id:
            raise BuildRecoveryError("repair requires bound DesignChange")
        if not any(item["id"] == repair_candidate_id
                   for item in self.propose_repairs(previous_attempt)):
            raise BuildRecoveryError("repair candidate not in previous attempt")
        profile = self.profiles.get(previous["profile_id"])
        if not profile:
            raise BuildRecoveryError("host profile unavailable")
        change = self._change(change_id, profile)
        if change.state.value not in {"PLANNED", "IMPLEMENTING", "EVIDENCE_MATCHED"}:
            raise BuildRecoveryError("DesignChange is not in an implementation state")
        source = _source_identity(profile)
        application_id = f"application-{uuid.uuid4().hex}"
        record = {"schema_version": "repair-application/1", "id": application_id,
                  "previous_attempt": previous_attempt,
                  "repair_candidate_id": repair_candidate_id,
                  "change_id": change_id, "change_version": change.version,
                  "design_revision": change.design_revision.id,
                  "actor_claim": actor, "source_revision_after_claim": _sha(_json(source)),
                  "source_files_after_claim": source,
                  "status": "UNVERIFIED", "authority": "DESIGN_ANNOTATION",
                  "created_ns": time.time_ns()}
        if self.application_recorder:
            from .design_lifecycle import RecordRepairApplication
            self.application_recorder(change_id, RecordRepairApplication(
                actor=actor, application_id=application_id,
                previous_attempt=previous_attempt,
                repair_candidate_id=repair_candidate_id,
                source_revision_after_claim=record["source_revision_after_claim"],
                expected_version=change.version))
        self._publish("applications", application_id, record)
        return record

    def correlate(self, attempt_id: str, store: Any) -> list[dict[str, Any]]:
        """Locate compiler spans against a pinned canonical snapshot.

        A shared name is never an identity proof. Source drift downgrades a
        unique span match to CANDIDATE_SET.
        """
        attempt = self.get_attempt(attempt_id)
        repo = store.repo(attempt["repo_id"])
        revision = attempt["canonical_revision"]
        root = Path(repo["root_path"]).resolve() if repo else None
        result: list[dict[str, Any]] = []
        for diagnostic in attempt["diagnostics"]:
            span = diagnostic["source_span"]
            row: dict[str, Any] = {"diagnostic_id": diagnostic["id"],
                                   "resolution": "UNKNOWN", "canonical_revision": revision,
                                   "canonical_entities": [], "design_change": attempt["change_id"],
                                   "design_revision": attempt["design_revision"],
                                   "dependency_evidence": [], "limitations": []}
            if not span or not root or not revision:
                row["limitations"].append("No pinned span, repo, or canonical revision")
                result.append(row)
                continue
            source = (Path(attempt["cwd"]) / span["path"]).resolve()
            if not source.is_relative_to(root):
                row["limitations"].append("Diagnostic path outside canonical repo")
                result.append(row)
                continue
            relative = source.relative_to(root).as_posix()
            nodes = store.nodes_by_path(attempt["repo_id"], revision, relative)
            line = span["start_line"]
            nodes = [node for node in nodes if node.get("start_line") is not None
                     and node.get("end_line") is not None
                     and int(node["start_line"]) <= line <= int(node["end_line"])]
            row["canonical_entities"] = [{"id": node["id"], "kind": node["kind"],
                                           "path": relative, "start_line": node["start_line"],
                                           "end_line": node["end_line"]} for node in nodes]
            file_state = store.file_state(attempt["repo_id"], relative)
            observed_hash = attempt["source_files"].get(relative)
            same_content = bool(file_state and observed_hash
                                and file_state.get("hash") == observed_hash)
            if len(nodes) == 1 and same_content:
                row["resolution"] = "EXACT"
            elif nodes:
                row["resolution"] = "CANDIDATE_SET"
                row["limitations"].append("Overlapping entities or source drift")
            else:
                row["limitations"].append("No canonical entity at compiler span")
            result.append(row)
        return result

    def assess_repair(self, attempt_id: str, *, test_run_id: str | None = None,
                      lifecycle_service: Any | None = None) -> dict[str, Any]:
        """Compute repair status; never rewrite the immutable BuildAttempt."""
        attempt = self.get_attempt(attempt_id)
        if attempt["verification_status"] != "BUILD_FIXED_BUT_UNVERIFIED":
            return {"status": attempt["verification_status"], "verified": False,
                    "test_run_id": None}
        if not test_run_id:
            return {"status": "BUILD_FIXED_BUT_UNVERIFIED", "verified": False,
                    "test_run_id": None}
        try:
            test = self.execution_authority.get_test_run(test_run_id)
            test_execution = self.execution_authority.get_receipt(test["execution_id"])
        except Exception:
            return {"status": "TEST_RUN_UNAVAILABLE", "verified": False,
                    "test_run_id": test_run_id}
        if (test["change_id"] != attempt["change_id"] or
                test["canonical_revision"] != attempt["canonical_revision"] or
                test["design_revision"] != attempt["design_revision"]):
            return {"status": "TEST_BINDING_MISMATCH", "verified": False,
                    "test_run_id": test_run_id}
        profile = self.profiles.get(attempt["profile_id"])
        try:
            current_source = _source_identity(profile) if profile else None
        except BuildRecoveryError:
            current_source = None
        if current_source != attempt["source_files"]:
            return {"status": "REPAIR_SOURCE_DRIFT", "verified": False,
                    "test_run_id": test_run_id}
        for name, digest in attempt["source_files"].items():
            test_file = test_execution["source_files_before"].get(name)
            if not test_file or test_file["sha256"] != digest:
                return {"status": "TEST_SOURCE_MISMATCH", "verified": False,
                        "test_run_id": test_run_id}
        if test["result"] == "FAIL":
            return {"status": "BUILD_FIXED_TEST_FAILED", "verified": False,
                    "test_run_id": test_run_id}
        if lifecycle_service is None:
            return {"status": "BUILD_FIXED_TEST_PASS_PENDING_LIFECYCLE",
                    "verified": False, "test_run_id": test_run_id}
        change = lifecycle_service.get_change(attempt["change_id"])
        receipts = lifecycle_service.repo.independent_receipts("test", change.id)
        adopted = [row for row in receipts if row.get("run_identity") == test_run_id
                   and row.get("result") == "PASS"
                   and row.get("design_revision") == attempt["design_revision"]
                   and row.get("canonical_revision") == attempt["canonical_revision"]]
        used = any(ref in {row["id"] for row in adopted}
                   for result in change.verification
                   for ref in result.get("evidence_refs", []))
        if change.state.value not in {"VERIFIED", "CLOSED"} or not adopted or not used:
            return {"status": "BUILD_FIXED_TEST_PASS_PENDING_LIFECYCLE",
                    "verified": False, "test_run_id": test_run_id}
        if change.scope.get("approval_required"):
            approvals = lifecycle_service.repo.independent_receipts("approval", change.id)
            latest = approvals[-1] if approvals else None
            if not latest or latest.get("decision") != "APPROVE" or (
                    latest.get("design_revision") != change.design_revision.id):
                return {"status": "BUILD_FIXED_TEST_PASS_PENDING_APPROVAL",
                        "verified": False, "test_run_id": test_run_id}
        return {"status": "VERIFIED_REPAIR", "verified": True,
                "test_run_id": test_run_id, "test_receipt_id": adopted[-1]["id"]}

    def failure_package(self, attempt_id: str, store: Any | None = None,
                        *, test_run_id: str | None = None,
                        lifecycle_service: Any | None = None) -> dict[str, Any]:
        attempt = self.get_attempt(attempt_id)
        if test_run_id is None and attempt["verification_status"] == "BUILD_FIXED_BUT_UNVERIFIED":
            # The read surface must derive the same latest relevant TEST
            # assessment as an explicit lookup. A newer failed test supersedes
            # an older pass; unrelated source/revision runs are ignored.
            for execution in self.execution_authority.list_receipts(
                    change_id=attempt["change_id"]):
                if (execution["kind"] != "TEST" or not execution["test_run_id"]
                        or execution["canonical_revision"] != attempt["canonical_revision"]
                        or execution["design_revision"] != attempt["design_revision"]):
                    continue
                files = execution["source_files_before"]
                if all(files.get(name, {}).get("sha256") == digest
                       for name, digest in attempt["source_files"].items()):
                    test_run_id = execution["test_run_id"]
                    break
        hypotheses = {
            "UNDEFINED_SYMBOL": ("MISSING_OBJECT_OR_SOURCE", "MISSING_LIBRARY_OR_LINK_FLAG",
                                 "ABI_OR_NAME_MANGLING_MISMATCH"),
            "MISSING_INCLUDE_OR_MODULE": ("SEARCH_PATH_MISSING", "DEPENDENCY_NOT_BUILT"),
            "DUPLICATE_SYMBOL": ("MULTIPLE_DEFINITIONS", "DUPLICATE_OBJECT_IN_LINK"),
            "TYPE_OR_INTERFACE_MISMATCH": ("DECLARATION_MISMATCH", "CALL_ARGUMENT_MISMATCH"),
            "SYNTAX_ERROR": ("SOURCE_SYNTAX", "LANGUAGE_MODE_MISMATCH"),
        }
        causes = [{"id": f"{item['id']}:cause:{index}",
                   "diagnostic_id": item["id"], "hypothesis": hypothesis,
                   "supporting_evidence": [item["raw_ref"]],
                   "contradicting_evidence": [], "scope": item["source_span"],
                   "authority": "INFERRED", "resolution": "CANDIDATE_SET"}
                  for item in attempt["diagnostics"]
                  for index, hypothesis in enumerate(
                      hypotheses.get(item["category"], ("CAUSE_UNKNOWN",)), 1)]
        return {"attempt": attempt, "diagnostics": attempt["diagnostics"],
                "evidence_correlations": self.correlate(attempt_id, store) if store else [],
                "root_cause_candidates": causes,
                "repair_candidates": self.propose_repairs(attempt_id),
                "verification": self.assess_repair(
                    attempt_id, test_run_id=test_run_id,
                    lifecycle_service=lifecycle_service),
                "limitations": ["No name-only canonical correlation is promoted to EXACT",
                                "Build PASS is not trusted tests PASS or design verification"]}
