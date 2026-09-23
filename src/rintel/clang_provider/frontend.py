"""Bounded machine-readable invocation of the real Clang frontend."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import time
from typing import Any, Callable

from .model import ClangIdentity, TranslationUnit


class FrontendStatus(str, Enum):
    COMPLETE = "COMPLETE"


class FrontendError(RuntimeError):
    pass


class UnsupportedAstSchema(FrontendError):
    pass


class UnsupportedDiagnosticSchema(FrontendError):
    pass


@dataclass(frozen=True)
class FrontendResult:
    status: FrontendStatus
    ast: dict[str, Any]
    diagnostics: dict[str, Any]
    clang_identity: ClangIdentity
    wall_seconds: float


Runner = Callable[..., subprocess.CompletedProcess[str]]


def detect_clang_identity(executable: str = "clang") -> ClangIdentity:
    resolved = shutil.which(executable) or executable
    path = Path(resolved).resolve()
    if not path.is_file():
        raise FrontendError(f"Clang executable does not exist: {path}")
    completed = subprocess.run(
        [str(path), "--version"], text=True, capture_output=True, check=True,
    )
    lines = completed.stdout.splitlines()
    version = lines[0].strip() if lines else ""
    target_line = next((line for line in lines if line.startswith("Target: ")), "")
    target = target_line.partition(": ")[2]
    if not version or not target:
        raise FrontendError("Clang version output lacks version or target")
    binary = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return ClangIdentity(str(path), version, target, binary)


def _run(argv: list[str], *, cwd: str, timeout: float
         ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, text=True, capture_output=True, timeout=timeout,
        check=False,
    )


class ClangFrontend:
    def __init__(self, *, runner: Runner = _run, timeout_s: float = 60.0,
                 max_output_bytes: int = 128 * 1024 * 1024):
        self.runner = runner
        self.timeout_s = timeout_s
        self.max_output_bytes = max_output_bytes

    def analyze(self, unit: TranslationUnit) -> FrontendResult:
        started = time.perf_counter()
        completed = self.runner(
            list(unit.analysis_arguments), cwd=unit.directory,
            timeout=self.timeout_s,
        )
        if completed.returncode != 0:
            raise FrontendError(
                f"Clang exited with status {completed.returncode}")
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout.encode()) + len(stderr.encode()) > self.max_output_bytes:
            raise FrontendError("Clang machine output exceeded byte limit")
        try:
            ast = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise UnsupportedAstSchema("Clang AST is not JSON") from exc
        if not isinstance(ast, dict) or ast.get("kind") != "TranslationUnitDecl" \
                or not isinstance(ast.get("inner"), list):
            raise UnsupportedAstSchema(
                "Clang AST root must be TranslationUnitDecl with inner array")
        try:
            diagnostics = json.loads(stderr)
        except json.JSONDecodeError as exc:
            raise UnsupportedDiagnosticSchema(
                "Clang diagnostics are not pure SARIF JSON") from exc
        if not isinstance(diagnostics, dict) \
                or diagnostics.get("version") != "2.1.0" \
                or not isinstance(diagnostics.get("runs"), list):
            raise UnsupportedDiagnosticSchema(
                "Clang diagnostics must be SARIF 2.1.0")
        return FrontendResult(
            status=FrontendStatus.COMPLETE,
            ast=ast,
            diagnostics=diagnostics,
            clang_identity=unit.clang,
            wall_seconds=time.perf_counter() - started,
        )


def dependency_paths(unit: TranslationUnit) -> tuple[Path, ...]:
    """Return compiler-produced dependency paths from Makefile-format output."""
    argv: list[str] = [unit.clang.executable]
    values = list(unit.analysis_arguments[1:])
    index = 0
    while index < len(values):
        token = values[index]
        if token == "-Xclang":
            index += 2
            continue
        if token in {"-fsyntax-only", "-Wno-sarif-format-unstable", unit.source} \
                or token.startswith("-fdiagnostics-format="):
            index += 1
            continue
        argv.append(token)
        index += 1
    argv.extend(("-M", "-MT", "__rintel__", unit.source))
    completed = subprocess.run(
        argv, cwd=unit.directory, text=True, capture_output=True,
        timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise FrontendError(
            f"Clang dependency scan exited with status {completed.returncode}")
    logical = completed.stdout.replace("\\\n", " ")
    if ":" not in logical:
        raise FrontendError("Clang dependency output lacks target separator")
    dependencies = shlex.split(logical.split(":", 1)[1])
    directory = Path(unit.directory)
    return tuple(dict.fromkeys(
        (Path(value) if Path(value).is_absolute() else directory / value).resolve()
        for value in dependencies
    ))


__all__ = [
    "ClangFrontend", "FrontendError", "FrontendResult", "FrontendStatus",
    "UnsupportedAstSchema", "UnsupportedDiagnosticSchema",
    "dependency_paths", "detect_clang_identity",
]
