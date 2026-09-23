"""CodeWritebackService — Proposed/Modified block -> code -> evidence (SPEC-P2
§12/§13/§14/§15).

The hard gate: a block only becomes Evidence through

    Flow Edit -> Generate Patch -> Write Working Tree -> Parse -> Reindex
    -> New Evidence Snapshot -> bind new canonical symbol -> existing

Failure (parse fail / symbol not found / ambiguous) keeps the block
proposed/modified and never fabricates a binding (§13).

FLOW0 writeback fixture language: Python (new-file writeback for proposed
blocks, span-replacement writeback for modified existing blocks; §12).
"""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

from ..store import FlowError, Store
from .domain import ensure_newline, new_file_diff, replace_diff
from .service import FlowService, snake_case


class CodeWritebackService:
    def __init__(self, store: Store, *, _lifecycle_write: bool = False):
        self.db = store
        self._lifecycle_write = _lifecycle_write

    @classmethod
    def _for_design_lifecycle(cls, store: Store) -> "CodeWritebackService":
        return cls(store, _lifecycle_write=True)

    # ------------------------------------------------------------------
    # preview (§14) — diff of exactly what apply would write
    # ------------------------------------------------------------------
    def preview(self, flow_id: str, block_id: str,
                code: str | None = None) -> dict:
        flow, block, repo, payload = self._prepare(flow_id, block_id, code)
        plan = self._plan_writeback(flow, block, repo, payload)
        if plan["mode"] == "new_file":
            diff = new_file_diff(plan["stem"], plan["relpath"],
                                 plan["content"])
        else:
            diff = replace_diff(plan["relpath"], plan["old_content"],
                                plan["new_content"])
        return {**plan, "diff": diff, "preview": "ok"}

    # ------------------------------------------------------------------
    # apply (§13): write working tree -> reindex -> rebind
    # ------------------------------------------------------------------
    def apply(self, flow_id: str, block_id: str,
              code: str | None = None,
              store_factory: Optional[Callable[[], Store]] = None) -> dict:
        if not self._lifecycle_write:
            raise FlowError(
                "design_lifecycle_required",
                "writeback apply requires DesignLifecycleService",
                {"required_change_context": "change_id"})
        flow, block, repo, payload = self._prepare(flow_id, block_id, code)
        plan = self._plan_writeback(flow, block, repo, payload)
        self._write_file(plan)
        errors: list[str] = []
        try:
            indexer = self._make_indexer(repo, store_factory)
            res = indexer.index()
        except Exception as exc:  # noqa: BLE001 — reindex failure is surfaced
            res = None
            errors.append(f"reindex_failed: {exc}")
        if res is None:
            self._record_failure(flow_id, block_id, errors)
            return self._apply_result(flow, block, plan, None, None, errors)
        sid = res.snapshot_id
        # rebind (never fabricate): find the canonical node in the new
        # snapshot by exact (path, name) identity.  A rename produces no
        # match -> the block stays modified with a failure note (§15: we
        # never rewrite Evidence identity by hand).
        node = self._find_node(repo["id"], sid, plan["relpath"],
                               block["name"])
        new_id = node["id"] if node else None
        if new_id is None:
            errors.append(
                "symbol_not_found: no canonical symbol matches the written "
                f"code in snapshot {sid} (file {plan['relpath']}, name "
                f"{block['name']})")
            self._record_failure(flow_id, block_id, errors)
            return self._apply_result(flow, block, plan, sid, None, errors)
        self.db.flow_put_binding(flow_id, block_id=block_id, snapshot_id=sid,
                                 canonical_symbol_id=new_id)
        # the block now IS the written symbol: lifecycle state -> existing
        # AND the block kind converges to the symbol's kind (a proposed
        # function block becomes a real function block — §13)
        from .domain import SYMBOL_TO_BLOCK_KIND
        new_kind = SYMBOL_TO_BLOCK_KIND.get(node["kind"], "function")
        self.db.flow_update_block(flow_id, block_id, state="existing",
                                  kind=new_kind)
        updated = self.db.flow_block(flow_id, block_id)
        return self._apply_result(flow, updated, plan, sid,
                                  {"snapshot_id": sid,
                                   "canonical_symbol_id": new_id,
                                   "node": {k: node[k] for k in (
                                       "id", "kind", "name", "qname",
                                       "language", "path")}
                                   if node else None}, errors)

    def _apply_result(self, flow, block, plan, sid, binding, errors):
        return {"ok": not errors and binding is not None,
                "mode": plan["mode"],
                "relpath": plan["relpath"],
                "snapshot_id": sid,
                "block": block,
                "binding": binding,
                "errors": errors}

    # ------------------------------------------------------------------
    def _prepare(self, flow_id: str, block_id: str,
                 code: str | None) -> tuple[dict, dict, dict, str]:
        svc = FlowService(self.db)
        flow = svc.require_flow(flow_id)
        block = self.db.flow_block(flow_id, block_id)
        if not block:
            raise FlowError("block_not_found", "block not found",
                            {"block_id": block_id})
        if block["state"] not in ("proposed", "modified"):
            raise FlowError(
                "block_not_editable",
                "only proposed or modified blocks can be written back",
                {"block_id": block_id, "state": block["state"]})
        repo = self.db.repo(flow["repo_id"])
        if not repo:
            raise FlowError("repo_not_found", "repo not found",
                            {"repo_id": flow["repo_id"]})
        payload = code if code is not None else block.get("code")
        if not payload or not payload.strip():
            raise FlowError("no_code", "no implementation code to write",
                            {"block_id": block_id})
        return flow, block, repo, payload

    def _plan_writeback(self, flow: dict, block: dict, repo: dict,
                        code: str) -> dict:
        if block["state"] == "proposed":
            stem = snake_case(block["name"]) or "flow_block"
            relpath = f"{stem}.py"
            full = os.path.join(repo["root_path"], relpath)
            if os.path.exists(full):
                raise FlowError(
                    "target_exists",
                    f"target file already exists: {relpath}",
                    {"relpath": relpath})
            content = ensure_newline(code)
            return {"mode": "new_file", "stem": stem, "relpath": relpath,
                    "full_path": full, "content": content,
                    "language": "python"}
        # modified: replace the definition span of the bound symbol
        binding = next((b for b in self.db.flow_bindings(flow["id"])
                        if b["block_id"] == block["id"]), None)
        if not binding:
            raise FlowError("not_bound", "modified block has no binding",
                            {"block_id": block["id"]})
        node = self.db.node_by_id(repo["id"], binding["snapshot_id"],
                                  binding["canonical_symbol_id"])
        if not node:
            raise FlowError("symbol_gone",
                            "bound symbol no longer exists in its snapshot",
                            {"canonical_symbol_id":
                             binding["canonical_symbol_id"]})
        full = os.path.join(repo["root_path"], node["path"])
        relpath = node["path"]
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                old_content = f.read()
        except OSError as exc:
            raise FlowError("file_unreadable", str(exc),
                            {"path": relpath}) from exc
        lines = old_content.splitlines(keepends=True)
        start = int(node["start_line"]) - 1
        end = int(node["end_line"])
        if start < 0 or end > len(lines) or start >= end:
            raise FlowError("span_stale",
                            "file has changed since the bound snapshot",
                            {"path": relpath, "start_line":
                             node["start_line"], "end_line": node["end_line"],
                             "lines": len(lines)})
        new_lines = lines[:start] + [ensure_newline(code)] + lines[end:]
        return {"mode": "replace_span", "relpath": relpath,
                "full_path": full, "old_content": old_content,
                "new_content": "".join(new_lines),
                "language": node["language"]}

    def _write_file(self, plan: dict) -> None:
        with open(plan["full_path"], "w", encoding="utf-8") as f:
            f.write(plan["new_content"] if plan["mode"] == "replace_span"
                    else plan["content"])

    def _make_indexer(self, repo: dict,
                      store_factory: Optional[Callable[[], Store]]):
        from ..indexer import Indexer
        if store_factory is None:
            from ..server.deps import build_store  # type: ignore
            store_factory = build_store
        gen = Indexer(store_factory(), repo["root_path"], repo["id"])
        try:
            return gen
        finally:
            pass  # Indexer owns + closes its store via begin/commit lifecycle

    def _find_node(self, repo_id: str, sid: str, relpath: str,
                   name: str) -> Optional[dict]:
        """Deterministic canonical lookup: exact path + name match (the
        writeback never guesses — path+name is the identity contract)."""
        for n in self.db.nodes_by_path(repo_id, sid, relpath):
            if n["name"] == name and n["kind"] in (
                    "FUNCTION", "METHOD", "CLASS", "MODULE", "SUBROUTINE"):
                return n
        return None

    def _record_failure(self, flow_id: str, block_id: str,
                        errors: list[str]) -> None:
        b = self.db.flow_block(flow_id, block_id)
        if not b:
            return
        meta = json.loads(b.get("meta_json") or "{}")
        meta["wb_error"] = "; ".join(errors)
        try:
            self.db.flow_update_block(flow_id, block_id, meta=meta)
        except Exception:  # noqa: BLE001 — failure note must not raise
            pass
