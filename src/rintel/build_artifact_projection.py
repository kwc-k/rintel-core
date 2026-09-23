"""Read-only, receipt-bound and size-limited BuildAttempt output projection."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_DISPLAY_BYTES = 65536
DEFAULT_DISPLAY_BYTES = 8192


class ArtifactReadError(ValueError):
    pass


def read_build_artifact(root: str | Path, attempt: dict[str, Any], kind: str,
                        artifact_id: str, expected_digest: str, *,
                        diagnostic_id: str | None = None,
                        max_bytes: int = DEFAULT_DISPLAY_BYTES) -> dict[str, Any]:
    """Never interpret a caller supplied value as a filesystem path."""
    attempt_id = attempt.get("attempt_id")
    if not isinstance(attempt_id, str) or not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt_id):
        raise ArtifactReadError("invalid BuildAttempt identity")
    if kind not in {"STDOUT", "STDERR"}:
        raise ArtifactReadError("invalid artifact kind")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DISPLAY_BYTES:
        raise ArtifactReadError("invalid output bound")
    stream = kind.lower()
    canonical_ref = f"attempts/{attempt_id}/{stream}.bin"
    receipt_digest = attempt.get(f"{stream}_sha256")
    if (artifact_id != canonical_ref or attempt.get(f"{stream}_ref") != canonical_ref
            or not isinstance(receipt_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt_digest)
            or expected_digest != receipt_digest):
        raise ArtifactReadError("artifact identity or digest binding mismatch")
    if diagnostic_id is not None:
        diagnostic = next((row for row in attempt.get("diagnostics", [])
                           if row.get("id") == diagnostic_id), None)
        if not diagnostic or diagnostic.get("attempt_id") != attempt_id or diagnostic.get("raw_ref") != canonical_ref:
            raise ArtifactReadError("diagnostic raw artifact binding mismatch")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_path = Path(root).resolve(strict=True)
        path = root_path / canonical_ref
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root_path):
            raise ArtifactReadError("artifact path is unavailable")
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactReadError("artifact is not a regular file")
            digest = hashlib.sha256()
            while chunk := file.read(65536):
                digest.update(chunk)
            after = os.fstat(file.fileno())
            if (digest.hexdigest() != receipt_digest or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ArtifactReadError("artifact bytes do not match immutable receipt")
            size = before.st_size
            file.seek(0)
            if size <= max_bytes:
                first, last = file.read(max_bytes), b""
            else:
                first_len = max_bytes // 2
                first = file.read(first_len)
                file.seek(- (max_bytes - first_len), os.SEEK_END)
                last = file.read(max_bytes - first_len)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ArtifactReadError):
            raise
        raise ArtifactReadError("artifact is unavailable") from exc
    try:
        first.decode("utf-8")
        last.decode("utf-8")
        encoding, content_type = "utf-8", "text/plain"
        render = lambda part: part.decode("utf-8")
    except UnicodeDecodeError:
        encoding, content_type = "hex-escaped", "application/octet-stream"
        render = lambda part: "".join(f"\\x{byte:02x}" for byte in part)
    omitted = size - len(first) - len(last)
    content = render(first)
    if omitted:
        content += f"\n[... {omitted} original bytes omitted ...]\n" + render(last)
    return {"attempt_id": attempt_id, "artifact_id": canonical_ref, "kind": kind,
            "content_type": content_type, "encoding": encoding, "size": size,
            "sha256": receipt_digest, "truncated": omitted > 0,
            "capture_truncated": bool(attempt.get(f"{stream}_truncated")),
            "max_bytes": max_bytes, "omitted_bytes": omitted, "content": content,
            "authority": "RAW_OBSERVED_DIAGNOSTIC"}
