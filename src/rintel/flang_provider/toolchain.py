"""Pinned, project-local LLVM Flang toolchain identity and extraction."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import tarfile

from .model import CompilerIdentity


VERSION = "22.1.7"
SOURCE_TAG = "llvmorg-22.1.7"
SOURCE_COMMIT = "a255c1ed36a1d06f79bd2633ba9f8d900153007c"
ARCHIVE_NAME = f"llvm-project-{VERSION}.src.tar.xz"
ARCHIVE_SIZE = 167_066_344
ARCHIVE_SHA256 = "5cc4a3f12bba50b6bdfb4b61bdc852117a0ff2517807c3902fc13267fb93562e"
ARCHIVE_URL = (
    "https://github.com/llvm/llvm-project/releases/download/"
    f"{SOURCE_TAG}/{ARCHIVE_NAME}"
)


class ArchiveVerificationError(RuntimeError):
    """The downloaded archive or its extraction target is not trustworthy."""


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_root(project_root: Path, name: str) -> Path:
    project = project_root.resolve()
    root = (project / ".rintel-toolchains" / name).resolve()
    if not root.is_relative_to(project):
        raise ArchiveVerificationError("toolchain root escapes project")
    return root


def source_root(project_root: Path) -> Path:
    return _bounded_root(project_root, f"llvm-{VERSION}-src")


def build_root(project_root: Path) -> Path:
    return _bounded_root(project_root, f"llvm-{VERSION}-build")


def toolchain_root(project_root: Path) -> Path:
    return _bounded_root(project_root, f"llvm-{VERSION}-install")


def preserve_driver_path(path: Path) -> Path:
    """Keep argv[0] spelling because clang++ symlink controls C++ linking."""
    return path.expanduser().absolute()


def cmake_arguments(project_root: Path) -> tuple[str, ...]:
    project = project_root.resolve()
    return (
        "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_INSTALL_PREFIX=" + str(toolchain_root(project)),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DLLVM_ENABLE_ASSERTIONS=ON",
        "-DLLVM_TARGETS_TO_BUILD=host",
        "-DLLVM_ENABLE_PROJECTS=clang;mlir;flang",
        "-DLLVM_INCLUDE_TESTS=OFF",
        "-DCLANG_INCLUDE_TESTS=OFF",
        "-DMLIR_INCLUDE_TESTS=OFF",
        "-DFLANG_INCLUDE_TESTS=OFF",
        "-DLLVM_INCLUDE_EXAMPLES=OFF",
        "-DLLVM_INCLUDE_BENCHMARKS=OFF",
    )


def verify_archive(path: Path) -> None:
    actual_size = path.stat().st_size
    if actual_size != ARCHIVE_SIZE:
        raise ArchiveVerificationError(
            f"archive size mismatch: expected {ARCHIVE_SIZE}, got {actual_size}")
    actual_digest = sha256_file(path)
    if actual_digest != ARCHIVE_SHA256:
        raise ArchiveVerificationError(
            f"archive SHA-256 mismatch: expected {ARCHIVE_SHA256}, "
            f"got {actual_digest}")


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveVerificationError(f"unsafe or invalid archive: {exc}") from exc


def detect_compiler_identity(executable: Path, *, role: str) -> CompilerIdentity:
    resolved = executable.resolve(strict=True)
    if not resolved.is_file() or not resolved.stat().st_mode & 0o111:
        raise FileNotFoundError(f"compiler is not executable: {resolved}")
    completed = subprocess.run(
        [str(resolved), "--version"], text=True, capture_output=True,
        timeout=30, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"compiler version probe failed ({completed.returncode}): "
            f"{completed.stderr[-1000:]}")
    output = completed.stdout + "\n" + completed.stderr
    version_match = re.search(
        r"(?:flang|GNU Fortran|Apple clang version|clang version)"
        r"[^\n]*?([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
        output,
    )
    target_match = re.search(r"^Target:\s*(\S+)", output, re.MULTILINE)
    if version_match is None:
        raise RuntimeError("compiler version output has no parseable version")
    return CompilerIdentity(
        role=role,
        executable=str(resolved),
        version=version_match.group(1),
        target=target_match.group(1) if target_match else "UNREPORTED",
        binary_sha256=sha256_file(resolved),
    )


def detect_project_flang(project_root: Path) -> CompilerIdentity:
    executable = toolchain_root(project_root) / "bin" / "flang"
    if not executable.is_file():
        raise FileNotFoundError(
            f"project-local Flang not installed at {executable}")
    identity = detect_compiler_identity(executable, role="semantic_probe")
    if identity.version != VERSION:
        raise RuntimeError(
            f"expected Flang {VERSION}, got {identity.version}")
    return identity


def detect_production_gfortran(
        executable: Path = Path("/opt/local/bin/gfortran")) -> CompilerIdentity:
    return detect_compiler_identity(executable, role="production_compiler")


__all__ = [
    "ARCHIVE_NAME", "ARCHIVE_SHA256", "ARCHIVE_SIZE", "ARCHIVE_URL",
    "ArchiveVerificationError", "SOURCE_COMMIT", "SOURCE_TAG", "VERSION",
    "build_root", "cmake_arguments", "detect_compiler_identity",
    "detect_production_gfortran", "detect_project_flang", "preserve_driver_path",
    "safe_extract",
    "sha256_file", "source_root", "toolchain_root", "verify_archive",
]
