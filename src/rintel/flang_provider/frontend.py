"""Bounded access to the pinned Flang frontend's primary artifacts.

Only HLFIR/FIR and compiler-driver dependency output are treated as candidate
machine interfaces.  Human diagnostics are hashed and retained as raw audit
text, never promoted to structured facts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Protocol

from .model import FortranTranslationUnit


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Cancellation(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class BoundedProcessResult:
    status: str
    command: tuple[str, ...]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    wall_seconds: float


@dataclass(frozen=True)
class FrontendResult:
    status: str
    command: tuple[str, ...]
    fir_path: str | None
    dependency_text: str | None
    diagnostics: tuple[dict, ...]
    stdout_sha256: str
    stderr_sha256: str
    wall_seconds: float
    peak_rss_bytes: int | None
    limitations: tuple[str, ...]
    raw_stderr: str


def _is_cancelled(cancellation: _Cancellation | None) -> bool:
    return cancellation is not None and cancellation.is_set()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_bounded(
        command: tuple[str, ...], *, cwd: Path, timeout_seconds: float,
        max_output_bytes: int, cancellation: _Cancellation | None = None,
        environment: dict[str, str] | None = None) -> BoundedProcessResult:
    """Run without unbounded pipe buffering and kill on limit/timeout/cancel."""
    started = time.monotonic()
    if _is_cancelled(cancellation):
        return BoundedProcessResult(
            "CANCELED", command, None, b"", b"", time.monotonic() - started)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        status: str | None = None
        while process.poll() is None:
            if _is_cancelled(cancellation):
                status = "CANCELED"
                _terminate(process)
                break
            if time.monotonic() - started > timeout_seconds:
                status = "TIMEOUT"
                _terminate(process)
                break
            if (stdout_file.tell() > max_output_bytes
                    or stderr_file.tell() > max_output_bytes):
                status = "OUTPUT_LIMIT"
                _terminate(process)
                break
            time.sleep(0.005)
        if status is None:
            status = "COMPLETE" if process.returncode == 0 else "FAILED"
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(max_output_bytes)
        stderr = stderr_file.read(max_output_bytes)
        if (stdout_file.read(1) or stderr_file.read(1)) and status == "COMPLETE":
            status = "OUTPUT_LIMIT"
        return BoundedProcessResult(
            status=status,
            command=command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            wall_seconds=time.monotonic() - started,
        )


class FlangFrontend:
    """Produce primary Flang artifacts while failing closed on unavailable APIs."""

    def __init__(self, *, timeout_seconds: float = 120,
                 max_output_bytes: int = 4 * 1024 * 1024) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def _layout(work_root: Path) -> tuple[Path, Path]:
        root = work_root.resolve()
        modules = root / "modules"
        fir = root / "fir"
        modules.mkdir(parents=True, exist_ok=True)
        fir.mkdir(parents=True, exist_ok=True)
        return modules, fir

    @staticmethod
    def _context_arguments(unit: FortranTranslationUnit,
                           modules: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        arguments: list[str] = []
        limitations: list[str] = []
        if unit.language_mode == "fixed":
            arguments.append("-ffixed-form")
        elif unit.language_mode == "free":
            arguments.append("-ffree-form")
        for raw in unit.raw_arguments:
            if raw == "-cpp":
                arguments.append(raw)
            elif raw == "-fPIC" or raw.startswith("-O"):
                limitations.append("NON_SEMANTIC_CODEGEN_FLAG_OMITTED:" + raw)
        for definition in unit.definitions:
            arguments.append("-D" + definition)
        search_paths = [modules, *(Path(value).resolve() for value in unit.module_paths),
                        *(Path(value).resolve() for value in unit.include_paths)]
        for path in dict.fromkeys(search_paths):
            arguments.extend(("-I", str(path)))
        return tuple(arguments), tuple(dict.fromkeys(limitations))

    @staticmethod
    def _result(process: BoundedProcessResult, *, fir_path: Path | None,
                dependency_text: str | None,
                limitations: tuple[str, ...]) -> FrontendResult:
        raw_stderr = process.stderr.decode("utf-8", errors="replace")
        return FrontendResult(
            status=process.status,
            command=process.command,
            fir_path=str(fir_path) if fir_path is not None else None,
            dependency_text=dependency_text,
            diagnostics=(),
            stdout_sha256=_sha256(process.stdout),
            stderr_sha256=_sha256(process.stderr),
            wall_seconds=process.wall_seconds,
            peak_rss_bytes=None,
            limitations=limitations,
            raw_stderr=raw_stderr,
        )

    def analyze(self, unit: FortranTranslationUnit, work_root: Path,
                cancellation: _Cancellation | None = None) -> FrontendResult:
        modules, fir = self._layout(work_root)
        context, context_limitations = self._context_arguments(unit, modules)
        source = Path(unit.source).resolve()
        output = fir / (source.stem + ".hlfir.mlir")
        command = (
            unit.semantic_frontend.executable,
            "-fc1",
            "-emit-hlfir",
            "-mmlir",
            "--mlir-print-debuginfo",
            *context,
            "-module-dir",
            str(modules),
            "-o",
            str(output),
            str(source),
        )
        process = run_bounded(
            command,
            cwd=Path(unit.directory).resolve(),
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            cancellation=cancellation,
        )
        limitations = (
            "UNSTABLE_FC1_INTERFACE",
            "STRUCTURED_DIAGNOSTICS_UNAVAILABLE",
            "PEAK_RSS_UNAVAILABLE_PER_PROCESS",
            *context_limitations,
        )
        if process.status != "COMPLETE" or not output.is_file():
            output.unlink(missing_ok=True)
            return self._result(
                process,
                fir_path=None,
                dependency_text=None,
                limitations=(*limitations, "MISSING_MODULE_NOT_MACHINE_CLASSIFIABLE"),
            )
        return self._result(
            process,
            fir_path=output,
            dependency_text=None,
            limitations=limitations,
        )

    def probe_dependencies(self, unit: FortranTranslationUnit, work_root: Path,
                           cancellation: _Cancellation | None = None) -> FrontendResult:
        modules, _ = self._layout(work_root)
        context, context_limitations = self._context_arguments(unit, modules)
        command = (
            unit.semantic_frontend.executable,
            "-M",
            *context,
            "-module-dir",
            str(modules),
            str(Path(unit.source).resolve()),
        )
        process = run_bounded(
            command,
            cwd=Path(unit.directory).resolve(),
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            cancellation=cancellation,
        )
        if process.status == "COMPLETE":
            return self._result(
                process,
                fir_path=None,
                dependency_text=process.stdout.decode("utf-8", errors="strict"),
                limitations=context_limitations,
            )
        unavailable = BoundedProcessResult(
            status="UNAVAILABLE",
            command=process.command,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            wall_seconds=process.wall_seconds,
        )
        return self._result(
            unavailable,
            fir_path=None,
            dependency_text=None,
            limitations=(
                "DEPENDENCY_OUTPUT_UNAVAILABLE",
                "STRUCTURED_DIAGNOSTICS_UNAVAILABLE",
                *context_limitations,
            ),
        )


__all__ = [
    "BoundedProcessResult", "FlangFrontend", "FrontendResult", "run_bounded",
]
