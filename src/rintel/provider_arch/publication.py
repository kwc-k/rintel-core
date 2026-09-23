"""Stage, validate, canonicalize, then atomically publish provider evidence."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from ._publication_batch import CanonicalPublicationBatch


class PublicationError(RuntimeError):
    pass


class CanonicalPublicationStore:
    """Filesystem reference implementation of the atomic publication seam."""

    def __init__(self, root: str | Path, *,
                 registry_version: str = "evidence-authority-registry/1",
                 rule_version: str = "evidence-authority-rules/1"):
        self.root = Path(root)
        self.revisions = self.root / "revisions"
        self.staging = self.root / ".staging"
        self.current_file = self.root / "CURRENT.json"
        self.registry_version = registry_version
        self.rule_version = rule_version

    def current_revision(self) -> str | None:
        if not self.current_file.exists():
            return None
        return json.loads(self.current_file.read_text())["revision"]

    def support_receipts(self, revision: str,
                         evidence_id: str) -> list[dict[str, Any]]:
        """Read the immutable support set; never aggregate its truth dimensions."""
        if not revision or revision in {".", ".."} or "/" in revision:
            raise PublicationError("invalid publication revision")
        bundle = self.revisions / revision / "canonical_evidence.json"
        if not bundle.is_file():
            raise PublicationError(f"revision not found: {revision}")
        payload = json.loads(bundle.read_text())
        return [dict(item) for item in payload.get("canonical_support_receipts", ())
                if item.get("canonical_fact_identity") == evidence_id]

    def publish(self, publication_revision: str,
                batch: CanonicalPublicationBatch) -> dict[str, Any]:
        """Publish only after every fallible stage has completed successfully."""
        if not publication_revision or "/" in publication_revision:
            raise PublicationError("invalid publication revision")
        if not isinstance(batch, CanonicalPublicationBatch):
            raise PublicationError(
                "publication requires a sealed CanonicalPublicationBatch")
        if batch.registry_version != self.registry_version:
            raise PublicationError(
                "CanonicalPublicationBatch registry version mismatch")
        if batch.rule_version != self.rule_version:
            raise PublicationError(
                "CanonicalPublicationBatch rule version mismatch")
        payload = {
            "revision": publication_revision,
            "input_revision": batch.input_revision,
            "provider": batch.provider,
            "provider_version": batch.provider_version,
            "coverage": dict(batch.coverage),
            "warnings": list(batch.warnings),
            "authority_receipt": {
                "registry_version": batch.registry_version,
                "rule_version": batch.rule_version,
                "canonical_state_before": dict(batch.canonical_state_before),
                "canonical_state_after": dict(batch.canonical_state_after),
                "admission_digests": list(batch.admission_digests),
            },
            **batch.canonical_payload,
        }
        payload["canonical_support_receipts"] = [{
            **dict(receipt),
            "canonical_revision": publication_revision,
            "receipt_id": "support:" + hashlib.sha256(
                json.dumps([publication_revision,
                            receipt["canonical_fact_identity"],
                            receipt["source_admission"], ordinal],
                           sort_keys=True).encode()).hexdigest(),
        } for ordinal, receipt in enumerate(batch.support_receipts)]

        self.revisions.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        target = self.revisions / publication_revision
        if target.exists():
            raise PublicationError(f"revision already exists: {publication_revision}")
        staged = Path(tempfile.mkdtemp(prefix=publication_revision + "-",
                                       dir=self.staging))
        try:
            bundle = staged / "canonical_evidence.json"
            bundle.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            # Validate the serialized artifact, not just the in-memory object.
            decoded = json.loads(bundle.read_text())
            if decoded.get("revision") != publication_revision:
                raise PublicationError("staged publication failed validation")
            os.replace(staged, target)
            pointer_tmp = self.root / f".CURRENT.{publication_revision}.tmp"
            pointer_tmp.write_text(json.dumps({"revision": publication_revision}) + "\n")
            os.replace(pointer_tmp, self.current_file)
        except Exception:
            if staged.exists():
                shutil.rmtree(staged)
            raise
        return payload
