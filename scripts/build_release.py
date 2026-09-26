"""Build a deterministic source release with both dependency locks."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "rintel-0.2.0"
FILES = (
    ".gitignore", "README.md", "README.zh-CN.md", "ROADMAP.md",
    "pyproject.toml", "uv.lock", "install.sh", "rintel",
    "uninstall.sh", "scripts/build_release.py", "web/package.json", "web/pnpm-lock.yaml",
    "web/pnpm-workspace.yaml", "web/index.html", "web/tsconfig.json",
    "web/vite.config.ts", "LICENSE", "NOTICE", "CONTRIBUTING.md",
    "SECURITY.md", "SUPPORT.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md",
    "TRADEMARKS.md",
)
DIRECTORIES = ("src/rintel", "web/src", "docs/acceptance")
EXCLUDED_SOURCE_DIRS = {"runtime_trace", "perf_topo"}


def source_files() -> list[Path]:
    files = [ROOT / item for item in FILES]
    for directory in DIRECTORIES:
        files.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and not path.is_symlink()
                     and not any(part in EXCLUDED_SOURCE_DIRS for part in path.parts)
                     and "__pycache__" not in path.parts
                     and path.suffix not in (".pyc", ".pyo")
                     and path.name != ".DS_Store")
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def build(output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in source_files():
                relative = path.relative_to(ROOT)
                data = path.read_bytes()
                info = tarfile.TarInfo(f"{PREFIX}/{relative.as_posix()}")
                info.size = len(data)
                info.mode = 0o755 if relative.as_posix() in ("install.sh", "rintel", "uninstall.sh") else 0o644
                info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = f"{digest}  {output.name}\n"
    output.with_suffix(output.suffix + ".sha256").write_text(checksum)
    (output.parent / "SHA256SUMS").write_text(checksum)
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "analysis_tournament" /
                        "release_packaging0" / "artifacts" / f"{PREFIX}-source.tar.gz")
    args = parser.parse_args()
    print(build(args.output))
