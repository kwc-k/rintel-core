"""Append-only, analysis-scoped staging for untrusted Provider batches."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .validation import ProtocolError

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class StagingArea:
    def __init__(self, root: str | Path, analysis_id: str):
        if not _SAFE_ID.fullmatch(analysis_id):
            raise ProtocolError("unsafe analysis_id")
        self.root = Path(root)
        self.analysis_id = analysis_id
        self.path = self.root / "active" / analysis_id
        self.facts_path = self.path / "facts.jsonl"
        self.batch_count = 0
        self.fact_count = 0

    def start(self, manifest: dict[str, Any]) -> None:
        self.path.mkdir(parents=True, exist_ok=False)
        self._write_json("manifest.json", manifest)
        self.facts_path.touch()

    def append_batch(self, sequence: int, facts: Iterable[dict[str, Any]]) -> None:
        if sequence != self.batch_count:
            raise ProtocolError(
                f"fact batch sequence mismatch: expected {self.batch_count}, received {sequence}")
        rows = list(facts)
        with self.facts_path.open("a", encoding="utf-8") as stream:
            for fact in rows:
                stream.write(json.dumps(fact, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.batch_count += 1
        self.fact_count += len(rows)

    def bind_session(self, provider: dict[str, Any],
                     contract: dict[str, Any]) -> None:
        """Persist the identities needed to interpret every later fact row."""
        self._write_json("provider.json", provider)
        self._write_json("contract.json", contract)

    def facts(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(line) for line in self.facts_path.read_text().splitlines())

    def complete(self, completion: dict[str, Any]) -> None:
        self._write_json("completion.json", completion)

    def quarantine(self, reason: str) -> Path:
        failed = self.root / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        target = failed / self.analysis_id
        if self.path.exists():
            self._write_json("failure.json", {"reason": reason})
            os.replace(self.path, target)
        return target

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        target = self.path / name
        tmp = self.path / (name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, target)


__all__ = ["StagingArea"]
