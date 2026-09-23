"""Explicit, single-store launch contract for browser acceptance runs only."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _verify_retained_bundle(manifest_path: Path, manifest: dict[str, Any],
                            store_path: Path) -> None:
    """Fail closed on a changed or cross-bound retained acceptance corpus."""
    root = manifest_path.parent.resolve(strict=True)
    relative_db = manifest.get("database")
    if not isinstance(relative_db, str) or (root / relative_db).resolve() != store_path.resolve():
        raise ValueError("E2E bundle datastore identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("E2E bundle file inventory is missing")
    actual_files = {str(path.relative_to(root)) for path in root.rglob("*")
                    if path.is_file() and path.name not in {
                        "BUNDLE_MANIFEST.json", "e2e_profile.json"}
                    and not path.name.endswith(("-wal", "-shm"))}
    if actual_files != set(files):
        raise ValueError("E2E bundle inventory has missing or unsealed files")
    if files.get(relative_db) != manifest.get("database_sha256"):
        raise ValueError("E2E bundle database hash contract mismatch")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("E2E bundle inventory is malformed")
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
            if (path.is_symlink() or not resolved.is_relative_to(root)
                    or not resolved.is_file()
                    or hashlib.sha256(resolved.read_bytes()).hexdigest() != expected):
                raise ValueError("E2E bundle file identity mismatch")
        except OSError as exc:
            raise ValueError("E2E bundle file unavailable") from exc
    compiler = manifest.get("compiler_identity", {})
    compiler_path = Path(compiler.get("path", ""))
    if (not compiler_path.is_file()
            or hashlib.sha256(compiler_path.read_bytes()).hexdigest() != compiler.get("sha256")):
        raise ValueError("E2E host compiler identity mismatch")
    code_root = Path(manifest.get("rintel_source_root", ""))
    for relative, expected in manifest.get("rintel_revision_identity", {}).items():
        code = code_root / relative
        if (not code.is_file()
                or hashlib.sha256(code.read_bytes()).hexdigest() != expected):
            raise ValueError("E2E Rintel revision identity mismatch")
    with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as db:
        for _, source_root in db.execute("SELECT id, root_path FROM repos"):
            resolved = Path(source_root).resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_dir():
                raise ValueError("E2E repository source root outside bundle")
        changes = {row[0]: (row[1], row[2]) for row in db.execute(
            "SELECT id, repo_id, base_canonical_revision FROM design_changes")}
        snapshots = {row[0]: row[1] for row in db.execute(
            "SELECT id, repo_id FROM snapshots")}
        for name in files:
            if not name.startswith("artifacts/build/attempts/") or not name.endswith("/record.json"):
                continue
            attempt = json.loads((root / name).read_text())
            change = changes.get(attempt.get("change_id"))
            cwd = Path(attempt.get("cwd", "")).resolve(strict=True)
            if (not change or change[0] != attempt.get("repo_id")
                    or snapshots.get(attempt.get("canonical_revision")) != change[0]
                    or not cwd.is_relative_to(root) or not cwd.is_dir()):
                raise ValueError("E2E BuildAttempt datastore/revision binding mismatch")
        for name in files:
            if not name.startswith("artifacts/execution/executions/") or not name.endswith("/receipt.json"):
                continue
            receipt = json.loads((root / name).read_text())
            change_id = receipt.get("change_id")
            if change_id and change_id not in changes:
                raise ValueError("E2E ExecutionReceipt datastore binding mismatch")
            revision = receipt.get("canonical_revision")
            if change_id and revision and snapshots.get(revision) != changes[change_id][0]:
                raise ValueError("E2E ExecutionReceipt revision binding mismatch")
        for name in files:
            if not name.startswith("artifacts/execution/executions/") or not name.endswith("/test_run.json"):
                continue
            receipt = json.loads((root / name).read_text())
            change_id = receipt.get("change_id")
            revision = receipt.get("canonical_revision")
            if not change_id or change_id not in changes or snapshots.get(revision) != changes[change_id][0]:
                raise ValueError("E2E TestRunReceipt datastore/revision binding mismatch")
    for attempt_id, paths in manifest.get("attempt_source_snapshots", {}).items():
        attempt_path = root / "artifacts/build/attempts" / attempt_id / "record.json"
        attempt = json.loads(attempt_path.read_text())
        for logical, relative in paths.items():
            if files.get(relative) != attempt.get("source_files", {}).get(logical):
                raise ValueError("E2E diagnostic source/attempt binding mismatch")
    for execution_id, paths in manifest.get("execution_source_snapshots", {}).items():
        receipt_path = root / "artifacts/execution/executions" / execution_id / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        for logical, relative in paths.items():
            expected = receipt.get("source_files_before", {}).get(logical, {}).get("sha256")
            if files.get(relative) != expected:
                raise ValueError("E2E execution source/receipt binding mismatch")


def load_e2e_profile() -> dict[str, Any] | None:
    configured = os.environ.get("RINTEL_E2E_PROFILE")
    if not configured:
        return None
    path = Path(configured).expanduser().resolve(strict=True)
    profile = json.loads(path.read_text())
    if not isinstance(profile, dict) or profile.get("schema") != "rintel-e2e-profile/1":
        raise ValueError("RINTEL_E2E_PROFILE has an unsupported schema")
    store = profile.get("store")
    if not isinstance(store, dict) or store.get("kind") != "sqlite":
        raise ValueError("E2E store must explicitly name one SQLite datastore")
    store_path = Path(store.get("path", ""))
    if not store_path.is_absolute() or not store_path.is_file():
        raise ValueError("E2E SQLite datastore must be an existing absolute file")
    for name in ("backend", "frontend"):
        endpoint = profile.get(name)
        if (not isinstance(endpoint, dict) or endpoint.get("host") != "127.0.0.1"
                or type(endpoint.get("port")) is not int
                or not 1 <= endpoint["port"] <= 65535):
            raise ValueError(f"E2E {name} must bind an explicit local port")
    if profile.get("mcp", {}).get("store") != "same":
        raise ValueError("E2E MCP must use the profile datastore")
    witness = profile.get("witness")
    if not isinstance(witness, dict) or not witness.get("bundle_id"):
        raise ValueError("E2E witness identity is required")
    manifest = Path(witness.get("manifest_path", ""))
    if (not manifest.is_absolute() or not manifest.is_file()
            or hashlib.sha256(manifest.read_bytes()).hexdigest() != witness.get("manifest_sha256")):
        raise ValueError("E2E witness manifest hash mismatch")
    manifest_data = json.loads(manifest.read_text())
    if manifest_data.get("bundle_version") == "rintel-e2e-witness-bundle/1":
        _verify_retained_bundle(manifest, manifest_data, store_path)
    return profile
