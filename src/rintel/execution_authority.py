"""One local, server-owned execution authority for bounded BUILD and TEST runs.

The public interface accepts a profile identity and finite, profile-declared
parameters. Callers cannot provide argv, environment, result, or receipts.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import selectors
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any


class ExecutionError(ValueError):
    pass


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ExecutionProfile:
    id: str
    version: str
    kind: str
    repo_id: str
    cwd: str
    allowed_root: str
    argv: tuple[str, ...]
    input_files: tuple[str, ...]
    timeout_seconds: int = 120
    stdout_limit_bytes: int = 1024 * 1024
    stderr_limit_bytes: int = 1024 * 1024
    artifact_files: tuple[str, ...] = ()
    artifact_limit_bytes: int = 8 * 1024 * 1024
    artifact_limit_count: int = 8
    environment_keys: tuple[str, ...] = (
        "SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "CPATH", "LIBRARY_PATH")
    parameter_choices: tuple[tuple[str, tuple[str, ...]], ...] = ()
    result_policy: str = "exit_zero"
    test_suite_id: str | None = None
    selection: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"BUILD", "TEST"} or not self.id or not self.version or not self.repo_id:
            raise ExecutionError("profile needs kind BUILD/TEST, id, version and repo")
        if not self.argv or not Path(self.argv[0]).is_absolute():
            raise ExecutionError("profile executable must be absolute")
        if "{" in self.argv[0] or Path(self.argv[0]).name in {
            "sh", "bash", "zsh", "fish", "env"}:
            raise ExecutionError("shell/env executables or parameterized executable forbidden")
        if not Path(self.allowed_root).is_absolute() or not Path(self.cwd).is_absolute():
            raise ExecutionError("working directory and root must be absolute")
        root, cwd = Path(self.allowed_root).resolve(), Path(self.cwd).resolve()
        if not cwd.is_relative_to(root) or not cwd.is_dir():
            raise ExecutionError("working directory outside allowed root")
        if not self.input_files:
            raise ExecutionError("profile must declare input files")
        if not 1 <= self.timeout_seconds <= 600:
            raise ExecutionError("timeout outside 1..600 seconds")
        if not 1 <= self.stdout_limit_bytes <= 16 * 1024 * 1024 or not (
                1 <= self.stderr_limit_bytes <= 16 * 1024 * 1024):
            raise ExecutionError("stdout/stderr cap outside bounded range")
        if not 0 <= self.artifact_limit_count <= 32 or len(self.artifact_files) > self.artifact_limit_count:
            raise ExecutionError("artifact count exceeds profile cap")
        if not 0 <= self.artifact_limit_bytes <= 256 * 1024 * 1024:
            raise ExecutionError("artifact bytes exceed profile cap")
        if self.result_policy not in {"exit_zero", "pytest", "unknown"}:
            raise ExecutionError("unsupported result policy")
        if self.kind == "TEST" and not self.test_suite_id:
            raise ExecutionError("TEST profile requires test suite identity")
        choices = dict(self.parameter_choices)
        if len(choices) != len(self.parameter_choices) or any(
                not name or not values or any(not isinstance(value, str) for value in values)
                for name, values in self.parameter_choices):
            raise ExecutionError("invalid finite parameter choices")
        placeholders = {token[1:-1] for token in self.argv if re.fullmatch(r"\{[a-z_]+\}", token)}
        if placeholders != set(choices):
            raise ExecutionError("parameter choices must match whole-argv placeholders")
        for token in self.argv:
            if "{" in token and not re.fullmatch(r"\{[a-z_]+\}", token):
                raise ExecutionError("partial argv interpolation forbidden")


@dataclass(frozen=True)
class ExecutionRequest:
    profile_id: str
    kind: str
    parameters: tuple[tuple[str, str], ...] = ()
    change_id: str | None = None
    canonical_revision: str | None = None
    design_revision: str | None = None
    expected_source_revision: str | None = None
    previous_execution: str | None = None
    lineage_ref: str | None = None
    source_git_commit: str | None = None
    source_tree: str | None = None
    workspace_id: str | None = None
    request_principal_id: str | None = None


class ExecutionAuthority:
    """The only process-launching seam for build and test evidence."""

    RUNNER_ID = "rintel-local-execution-authority"
    RUNNER_VERSION = "1"

    def __init__(self, root: str | Path, profiles: tuple[ExecutionProfile, ...]):
        self.root = Path(root).resolve()
        if len({profile.id for profile in profiles}) != len(profiles):
            raise ExecutionError("duplicate execution profile id")
        self.profiles = {profile.id: profile for profile in profiles}

    @staticmethod
    def _inputs(profile: ExecutionProfile) -> tuple[dict[str, dict[str, Any]], str]:
        root = Path(profile.allowed_root).resolve()
        cwd = Path(profile.cwd).resolve()
        files: dict[str, dict[str, Any]] = {}
        for name in profile.input_files:
            path = (cwd / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ExecutionError(f"input unavailable or outside root: {name}")
            files[name] = {"sha256": _hash_file(path), "size": path.stat().st_size}
        return files, _sha(_json(files))

    @staticmethod
    def _argv(profile: ExecutionProfile, request: ExecutionRequest) -> tuple[str, ...]:
        supplied = dict(request.parameters)
        allowed = dict(profile.parameter_choices)
        if len(supplied) != len(request.parameters) or set(supplied) != set(allowed):
            raise ExecutionError("parameters must exactly match profile choices")
        if any(value not in allowed[name] for name, value in supplied.items()):
            raise ExecutionError("parameter value not in finite profile choices")
        return tuple(supplied[token[1:-1]] if token.startswith("{") else token
                     for token in profile.argv)

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @classmethod
    def _bounded_process(cls, argv: tuple[str, ...], profile: ExecutionProfile,
                         cancel: Event | None) -> tuple[bytes, bytes, int | None, str, bool, bool]:
        process = subprocess.Popen(argv, cwd=profile.cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        streams = {process.stdout: (bytearray(), profile.stdout_limit_bytes),
                   process.stderr: (bytearray(), profile.stderr_limit_bytes)}
        for pipe in streams:
            assert pipe is not None
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        reason = "COMPLETED"
        truncated = {process.stdout: False, process.stderr: False}
        deadline = time.monotonic() + profile.timeout_seconds
        try:
            while selector.get_map() or process.poll() is None:
                if reason == "COMPLETED" and cancel is not None and cancel.is_set():
                    reason = "CANCELLED"
                    cls._stop(process)
                if reason == "COMPLETED" and time.monotonic() >= deadline:
                    reason = "TIMEOUT"
                    cls._stop(process)
                for key, _ in selector.select(0.05):
                    pipe = key.fileobj
                    try:
                        chunk = os.read(pipe.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    data, cap = streams[pipe]
                    room = max(0, cap - len(data))
                    data.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated[pipe] = True
                        if reason == "COMPLETED":
                            reason = "OUTPUT_LIMIT"
                            cls._stop(process)
                if reason != "COMPLETED" and time.monotonic() >= deadline + 2:
                    for key in list(selector.get_map().values()):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    break
            process.wait(timeout=2)
        finally:
            selector.close()
            if process.poll() is None:
                cls._stop(process)
                process.wait(timeout=2)
        return (bytes(streams[process.stdout][0]), bytes(streams[process.stderr][0]),
                process.returncode, reason, truncated[process.stdout],
                truncated[process.stderr])

    @staticmethod
    def _result(kind: str, policy: str, exit_code: int | None, reason: str) -> str:
        if reason == "TIMEOUT":
            return "TIMEOUT"
        if reason == "CANCELLED":
            return "CANCELLED"
        if reason in {"INPUT_DRIFT", "INPUT_UNAVAILABLE"}:
            return "UNKNOWN"
        if reason != "COMPLETED" or exit_code is None:
            return "ERROR"
        if policy == "unknown":
            return "UNKNOWN"
        if policy == "pytest" and kind == "TEST":
            return "PASS" if exit_code == 0 else "FAIL" if exit_code == 1 else "ERROR"
        if policy == "exit_zero":
            return "PASS" if exit_code == 0 else "FAIL"
        return "UNKNOWN"

    def _artifacts(self, profile: ExecutionProfile) -> tuple[list[dict[str, Any]],
                                                             dict[str, bytes], bool]:
        rows: list[dict[str, Any]] = []
        blobs: dict[str, bytes] = {}
        used = 0
        limit_hit = False
        root = Path(profile.allowed_root).resolve()
        for index, name in enumerate(profile.artifact_files):
            path = (Path(profile.cwd) / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                rows.append({"path": name, "status": "UNAVAILABLE"})
                continue
            size = path.stat().st_size
            if used + size > profile.artifact_limit_bytes:
                rows.append({"path": name, "status": "ARTIFACT_LIMIT", "size": size})
                limit_hit = True
                continue
            content = path.read_bytes()
            used += len(content)
            ref = f"artifacts/{index}.bin"
            blobs[ref] = content
            rows.append({"path": name, "status": "CAPTURED", "size": len(content),
                         "sha256": _sha(content), "ref": ref})
        return rows, blobs, limit_hit

    def _publish(self, execution_id: str, receipt: dict[str, Any],
                 blobs: dict[str, bytes], test_run: dict[str, Any] | None) -> None:
        base = self.root / "executions"
        base.mkdir(parents=True, exist_ok=True)
        target = base / execution_id
        pending = base / f".pending-{uuid.uuid4().hex}"
        pending.mkdir()
        try:
            (pending / "receipt.json").write_bytes(_json(receipt))
            (pending / "stdout.bin").write_bytes(blobs.pop("stdout.bin"))
            (pending / "stderr.bin").write_bytes(blobs.pop("stderr.bin"))
            for name, content in blobs.items():
                path = pending / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            if test_run:
                (pending / "test_run.json").write_bytes(_json(test_run))
            pending.rename(target)
        except Exception:
            shutil.rmtree(pending)
            raise

    def execute(self, request: ExecutionRequest, *, cancel: Event | None = None) -> dict[str, Any]:
        profile = self.profiles.get(request.profile_id)
        if profile is None or profile.kind != request.kind:
            raise ExecutionError("unknown profile or kind mismatch")
        argv = self._argv(profile, request)
        executable = Path(argv[0]).resolve()
        if not executable.is_file():
            raise ExecutionError("profile executable unavailable")
        if request.previous_execution:
            previous = self.get_receipt(request.previous_execution)
            if previous["repo_id"] != profile.repo_id or previous["change_id"] != request.change_id:
                raise ExecutionError("previous execution binding mismatch")
        execution_id = f"execution-{uuid.uuid4().hex}"
        started_ns = time.time_ns()
        try:
            source_files_before, source_revision_before = self._inputs(profile)
        except ExecutionError:
            source_files_before, source_revision_before = {}, None
            reason = "INPUT_UNAVAILABLE"
        else:
            reason = ("INPUT_DRIFT" if request.expected_source_revision is not None
                      and request.expected_source_revision != source_revision_before
                      else "COMPLETED")
        env = {key: os.environ.get(key, "") for key in profile.environment_keys}
        version = "UNAVAILABLE"
        # Compiler/interpreter version is useful but must obey the same output
        # and time bounds as the authoritative run. Arbitrary test executables
        # are not probed: --version could itself execute a test or mutate state.
        if executable.name in {"gcc", "g++", "clang", "clang++", "gfortran",
                               "python", "python3", "python3.14", "pytest"}:
            try:
                version_profile = replace(profile, timeout_seconds=min(5, profile.timeout_seconds),
                                          stdout_limit_bytes=4096, stderr_limit_bytes=4096)
                version_out, version_err, _, version_reason, _, _ = self._bounded_process(
                    (str(executable), "--version"), version_profile, None)
                if version_reason == "COMPLETED":
                    lines = (version_out or version_err).decode(errors="replace").splitlines()
                    version = lines[0][:300] if lines else "UNAVAILABLE"
            except (OSError, subprocess.SubprocessError):
                pass
        tool_identity = {"path": str(executable), "sha256": _hash_file(executable),
                         "version": version}
        stdout = stderr = b""
        exit_code: int | None = None
        stdout_truncated = stderr_truncated = False
        invoked = False
        if reason == "COMPLETED":
            try:
                invoked = True
                stdout, stderr, exit_code, reason, stdout_truncated, stderr_truncated = (
                    self._bounded_process(argv, profile, cancel))
            except (OSError, subprocess.SubprocessError):
                reason = "RUNNER_ERROR"
            try:
                source_files_after, source_revision_after = self._inputs(profile)
            except ExecutionError:
                source_files_after, source_revision_after = {}, None
            if source_revision_after != source_revision_before:
                reason = "INPUT_DRIFT"
        else:
            source_files_after, source_revision_after = source_files_before, source_revision_before
        artifacts, artifact_blobs, artifact_limit = self._artifacts(profile) if invoked else ([], {}, False)
        if artifact_limit and reason == "COMPLETED":
            reason = "ARTIFACT_LIMIT"
        finished_ns = time.time_ns()
        result = self._result(profile.kind, profile.result_policy, exit_code, reason)
        test_run_id = (f"test-run-{execution_id.removeprefix('execution-')}"
                       if profile.kind == "TEST" and result in {"PASS", "FAIL"}
                       and reason == "COMPLETED" else None)
        receipt: dict[str, Any] = {
            "schema_version": "execution-receipt/1", "execution_id": execution_id,
            "kind": profile.kind, "profile_id": profile.id, "profile_version": profile.version,
            "repo_id": profile.repo_id, "change_id": request.change_id,
            "source_revision": source_revision_before,
            "source_revision_after": source_revision_after,
            "source_files_before": source_files_before,
            "source_files_after": source_files_after,
            "input_closure": "PARTIAL", "canonical_revision": request.canonical_revision,
            "design_revision": request.design_revision,
            "cwd": str(Path(profile.cwd).resolve()), "command_identity": _sha(_json(argv)),
            "resolved_argv": list(argv), "parameters": dict(request.parameters),
            "environment_identity": _sha(_json({"all_env": dict(os.environ),
                                                "os": platform.platform(),
                                                "architecture": platform.machine()})),
            "selected_environment": env, "full_environment_closure": "PARTIAL",
            "os": platform.system(), "os_release": platform.release(),
            "architecture": platform.machine(), "tool_identity": tool_identity,
            "started_ns": started_ns, "finished_ns": finished_ns,
            "duration_ms": round((finished_ns - started_ns) / 1e6, 3),
            "exit_code": exit_code, "termination_reason": reason, "result": result,
            "stdout_ref": f"executions/{execution_id}/stdout.bin",
            "stdout_sha256": _sha(stdout), "stdout_truncated": stdout_truncated,
            "stderr_ref": f"executions/{execution_id}/stderr.bin",
            "stderr_sha256": _sha(stderr), "stderr_truncated": stderr_truncated,
            "raw_output_complete": not stdout_truncated and not stderr_truncated,
            "artifacts": artifacts, "runner_invoked": invoked,
            "runner_identity": self.RUNNER_ID, "runner_version": self.RUNNER_VERSION,
            "runner_code_sha256": _hash_file(Path(__file__)),
            "previous_execution": request.previous_execution,
            "lineage_ref": request.lineage_ref,
            "source_git_commit": request.source_git_commit,
            "source_tree": request.source_tree,
            "workspace_id": request.workspace_id,
            "request_principal_id": request.request_principal_id,
            "test_run_id": test_run_id,
            "test_suite_id": profile.test_suite_id,
            "selection": profile.selection,
            "authority": ("SERVER_EXECUTION" if reason == "COMPLETED"
                          else "INVALID_FOR_PASS"),
        }
        test_run = None
        if test_run_id:
            test_run = {
                "schema_version": "test-run-receipt/1", "test_run_id": test_run_id,
                "execution_id": execution_id, "change_id": request.change_id,
                "source_revision": source_revision_before,
                "canonical_revision": request.canonical_revision,
                "design_revision": request.design_revision,
                "runner_identity": self.RUNNER_ID, "runner_version": self.RUNNER_VERSION,
                "profile_id": profile.id, "profile_version": profile.version,
                "test_suite_id": profile.test_suite_id, "selection": profile.selection,
                "result": result, "started_ns": started_ns, "finished_ns": finished_ns,
                "exit_code": exit_code,
                "evidence_refs": [receipt["stdout_ref"], receipt["stderr_ref"]],
                "authority": "SERVER_EXECUTION",
            }
        self._publish(execution_id, receipt,
                      {"stdout.bin": stdout, "stderr.bin": stderr, **artifact_blobs}, test_run)
        return receipt

    def execute_in_git_worktree(
            self, request: ExecutionRequest, *, workspace_root: str | Path,
            expected_git_commit: str, workspace_id: str,
            principal_id: str, cancel: Event | None = None) -> dict[str, Any]:
        """Execute a host-owned profile against one clean, exact Git tree.

        The caller selects only a registered profile.  Git identity and the
        workspace-relative cwd are resolved here, before any process starts.
        """
        profile = self.profiles.get(request.profile_id)
        if profile is None or profile.kind != request.kind:
            raise ExecutionError("unknown profile or kind mismatch")
        workspace = Path(workspace_root).resolve()
        if not workspace.is_dir():
            raise ExecutionError("workspace unavailable")

        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-c", f"safe.directory={workspace}", "-C",
                 str(workspace), *args], text=True, capture_output=True,
                check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if result.returncode:
                raise ExecutionError("workspace Git identity unavailable")
            return result.stdout.strip()

        top = Path(git("rev-parse", "--show-toplevel")).resolve()
        if top != workspace:
            raise ExecutionError("workspace root is not the Git worktree root")
        head = git("rev-parse", "HEAD")
        if head != expected_git_commit:
            raise ExecutionError("workspace head changed")
        if git("status", "--porcelain=v1"):
            raise ExecutionError("workspace is dirty")
        tree = git("rev-parse", "HEAD^{tree}")

        configured_root = Path(profile.allowed_root).resolve()
        configured_cwd = Path(profile.cwd).resolve()
        try:
            cwd_suffix = configured_cwd.relative_to(configured_root)
        except ValueError as exc:
            raise ExecutionError("profile cwd is outside configured root") from exc
        bound_cwd = (workspace / cwd_suffix).resolve()
        if not bound_cwd.is_relative_to(workspace) or not bound_cwd.is_dir():
            raise ExecutionError("workspace profile cwd unavailable")
        bound_profile = replace(profile, allowed_root=str(workspace),
                                cwd=str(bound_cwd))
        bound_request = replace(
            request, source_git_commit=head, source_tree=tree,
            workspace_id=workspace_id, request_principal_id=principal_id)
        return ExecutionAuthority(self.root, (bound_profile,)).execute(
            bound_request, cancel=cancel)

    def source_revision(self, profile_id: str) -> str:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ExecutionError("unknown profile")
        return self._inputs(profile)[1]

    def get_receipt(self, execution_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"execution-[0-9a-f]{32}", execution_id):
            raise ExecutionError("invalid execution id")
        path = self.root / "executions" / execution_id / "receipt.json"
        if not path.is_file():
            raise ExecutionError("execution receipt unavailable")
        return json.loads(path.read_text())

    def list_receipts(self, *, change_id: str | None = None) -> list[dict[str, Any]]:
        base = self.root / "executions"
        if not base.is_dir():
            return []
        rows = [json.loads(path.read_text()) for path in
                base.glob("execution-*/receipt.json")]
        if change_id is not None:
            rows = [row for row in rows if row["change_id"] == change_id]
        return sorted(rows, key=lambda row: row["started_ns"], reverse=True)

    def raw_output(self, execution_id: str, stream: str) -> bytes:
        self.get_receipt(execution_id)
        if stream not in {"stdout", "stderr"}:
            raise ExecutionError("invalid stream")
        return (self.root / "executions" / execution_id / f"{stream}.bin").read_bytes()

    def get_test_run(self, test_run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"test-run-[0-9a-f]{32}", test_run_id):
            raise ExecutionError("invalid test run id")
        execution_id = "execution-" + test_run_id.removeprefix("test-run-")
        execution = self.get_receipt(execution_id)
        path = self.root / "executions" / execution_id / "test_run.json"
        if (execution["kind"] != "TEST" or execution["test_run_id"] != test_run_id
                or execution["result"] not in {"PASS", "FAIL"} or not path.is_file()):
            raise ExecutionError("trusted test run unavailable")
        receipt = json.loads(path.read_text())
        if receipt["execution_id"] != execution_id or receipt["result"] != execution["result"]:
            raise ExecutionError("test run and execution receipt mismatch")
        return receipt

    def test_run_lookup(self, test_run_id: str) -> dict[str, Any] | None:
        try:
            receipt = self.get_test_run(test_run_id)
            # A historical PASS remains a historical receipt, but it is not
            # admissible as proof for a workspace whose declared inputs moved.
            if self.source_revision(receipt["profile_id"]) != receipt["source_revision"]:
                return None
        except ExecutionError:
            return None
        return {"run_id": receipt["test_run_id"],
                "execution_id": receipt["execution_id"],
                "change_id": receipt["change_id"],
                "design_revision": receipt["design_revision"],
                "canonical_revision": receipt["canonical_revision"],
                "source_revision": receipt["source_revision"],
                "test_identity": receipt["test_suite_id"],
                "result": receipt["result"],
                "timestamp": receipt["finished_ns"] // 1_000_000,
                "evidence_refs": receipt["evidence_refs"],
                "issuer": self.RUNNER_ID}
