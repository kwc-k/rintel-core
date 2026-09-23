"""Public command seam for the Rintel design-to-evidence lifecycle."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from .design_drc import run_design_drc
from .models import (
    AbandonChange,
    AdoptIndexJob,
    AcceptanceCriterion,
    BeginImplementation,
    ChangeState,
    CloseChange,
    DesignChange,
    DesignMutation,
    DesignRef,
    DesignRevision,
    EvidenceBrief,
    EvaluateExpectedActual,
    LifecycleReceipt,
    PlanChange,
    RecordReindex,
    RecordRepairApplication,
    RequestReindex,
    RunReindex,
    VerifyChange,
)
from .repository import LifecycleRepository
from .source_projection import project_source_span


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class DesignLifecycleError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class DesignLifecycleService:
    """Only public write seam for lifecycle-associated design changes."""

    def __init__(self, store: Any, *, clock: Callable[[], int] | None = None,
                 lvs_runner: Callable[[DesignChange], dict] | None = None,
                 reindex_runner: Callable[[DesignChange], Any] | None = None,
                 reference_provider: Callable[[str, str, dict], list[dict]]
                 | None = None,
                 runtime_provider: Callable[[str, str, dict], list[dict]]
                 | None = None,
                 job_lookup: Callable[[str], dict | None] | None = None,
                 authorization_lookup: Callable[[str, DesignChange], dict | None]
                 | None = None,
                 test_run_lookup: Callable[[str], dict | None] | None = None):
        self.store = store
        self.repo = LifecycleRepository(store)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.lvs_runner = lvs_runner or self._default_lvs
        self.reindex_runner = reindex_runner or self._default_reindex
        self.reference_provider = reference_provider or (
            lambda _repo, _sid, _scope: [])
        self.runtime_provider = runtime_provider or (
            lambda _repo, _sid, _scope: [])
        self.job_lookup = job_lookup
        self.authorization_lookup = authorization_lookup
        self.test_run_lookup = test_run_lookup

    def record_test_run(self, change_id: str, run_id: str) -> dict[str, Any]:
        """Bind independently verified runner output, not a human PASS claim."""
        change = self.get_change(change_id)
        if change.state in {ChangeState.CLOSED, ChangeState.ABANDONED}:
            raise DesignLifecycleError("terminal_change", "terminal change is immutable")
        run = self.test_run_lookup(run_id) if self.test_run_lookup else None
        if (change.implementation or {}).get("observation_status") != "recorded":
            raise DesignLifecycleError("reindex_required", "adopt actual before test run")
        actual = (change.implementation or {}).get("current_evidence_revision")
        if not run or run.get("run_id") != run_id:
            raise DesignLifecycleError("test_run_unavailable", "no trusted test run")
        if (run.get("change_id") not in {None, change.id} or
                run.get("design_revision") not in {None, change.design_revision.id}):
            raise DesignLifecycleError("test_run_binding_mismatch",
                                       "test run change or design revision mismatch")
        if run.get("canonical_revision") != actual or actual != self._active_revision(
                change.repo_id):
            raise DesignLifecycleError("test_run_binding_mismatch", "test run revision is stale")
        if run.get("result") not in {"PASS", "FAIL"} or not run.get(
                "test_identity") or not run.get("evidence_refs"):
            raise DesignLifecycleError("test_run_incomplete", "test run lacks proof")
        receipt = {
            "id": f"test-receipt-{uuid.uuid4().hex}", "change_id": change.id,
            "run_identity": run_id, "subject": change.id,
            "design_revision": change.design_revision.id,
            "canonical_revision": actual, "test_identity": run["test_identity"],
            "result": run["result"], "timestamp": run.get("timestamp") or self.clock(),
            "evidence_refs": list(run["evidence_refs"]),
        }
        self.repo.append_independent_receipt("test", receipt)
        return receipt

    def record_approval(self, change_id: str, *, authorization_identity: str,
                        decision: str, expected_version: int) -> dict[str, Any]:
        """Fail closed unless a trusted authorization adapter resolves identity."""
        change = self.get_change(change_id)
        if change.state in {ChangeState.CLOSED, ChangeState.ABANDONED}:
            raise DesignLifecycleError("terminal_change", "terminal change is immutable")
        if change.version != expected_version:
            raise DesignLifecycleError("stale_preflight", "change version has advanced")
        auth = self._approval_authorization(change, authorization_identity)
        if not auth:
            raise DesignLifecycleError("approver_authorization_unavailable",
                                       "authenticated approver permission required")
        if decision not in {"APPROVE", "REJECT"}:
            raise DesignLifecycleError("invalid_approval_decision", "invalid decision")
        prior = self.repo.independent_receipts("approval", change.id)
        timestamp = max(self.clock(), prior[-1]["timestamp"] + 1 if prior else 0)
        approval_id = f"approval-receipt-{uuid.uuid4().hex}"
        receipt = {
            "id": approval_id, "approval_id": approval_id,
            "change_id": change.id,
            "design_revision": change.design_revision.id,
            "approver_identity": auth["subject"],
            "principal_id": auth["principal_id"],
            "principal_type": auth["principal_type"],
            "auth_session_id": auth["auth_session_id"],
            "rintel_instance_id": auth["rintel_instance_id"],
            "auth_method": auth["auth_method"],
            "authorization_identity": auth["authorization_id"],
            "policy_version": auth["policy_version"], "decision": decision,
            "timestamp": timestamp, "change_version": change.version,
            "applicable_subject": {
                "change_id": change.id, "repo_id": change.repo_id,
                "design_revision": change.design_revision.id,
                "scope_digest": _digest(change.scope),
            },
        }
        receipt["receipt_digest"] = _digest(receipt)
        self.repo.append_independent_receipt("approval", receipt)
        return receipt

    def _approval_authorization(self, change: DesignChange,
                                identity: str | None) -> dict[str, Any] | None:
        auth = (self.authorization_lookup(identity, change)
                if identity and self.authorization_lookup else None)
        if (not auth or auth.get("permission") != "approve_design" or
                auth.get("principal_type") != "LOCAL_OWNER" or
                auth.get("auth_method") != "BOOTSTRAP_TOKEN" or
                not all(auth.get(key) for key in (
                    "subject", "policy_version", "authorization_id",
                    "principal_id", "auth_session_id", "rintel_instance_id"))):
            return None
        return auth

    def open_change(
        self, *, repo_id: str, base_canonical_revision: str, intent: str,
        scope: dict[str, Any],
        acceptance_criteria: list[AcceptanceCriterion], actor: str,
        architecture_proposal_ref: DesignRef | None = None,
        flow_model_ref: DesignRef | None = None,
    ) -> DesignChange:
        snapshot = self.store.snapshot(base_canonical_revision)
        if not snapshot or snapshot.get("repo_id") != repo_id:
            raise DesignLifecycleError(
                "invalid_base_revision", "base canonical revision is not in repo",
                {"repo_id": repo_id, "revision": base_canonical_revision},
            )
        if not intent.strip():
            raise DesignLifecycleError("intent_required", "change intent is required")
        now = self.clock()
        cid = f"change-{uuid.uuid4().hex}"
        rev_payload = {
            "change_id": cid,
            "architecture_proposal_ref": (
                vars(architecture_proposal_ref) if architecture_proposal_ref else None),
            "flow_model_ref": vars(flow_model_ref) if flow_model_ref else None,
            "expected_changes": [],
        }
        revision = DesignRevision(
            id=f"design-rev-{uuid.uuid4().hex}", change_id=cid,
            authority="DESIGN_ANNOTATION",
            architecture_proposal_ref=architecture_proposal_ref,
            flow_model_ref=flow_model_ref, expected_changes=(), created_at=now,
            digest=_digest(rev_payload),
        )
        brief = self._build_brief(repo_id, base_canonical_revision, scope)
        change = DesignChange(
            id=cid, repo_id=repo_id,
            base_canonical_revision=base_canonical_revision,
            intent=intent.strip(), scope=dict(scope),
            acceptance_criteria=tuple(acceptance_criteria),
            design_revision=revision, evidence_brief=brief,
            state=ChangeState.OPEN,
            remaining_unknowns=brief.unknowns,
            created_at=now, updated_at=now,
        )
        receipt = self._receipt(change, 1, "open_change", actor, None,
                                {"design_revision": revision.id})
        self.repo.insert(change, receipt)
        return change

    def apply_command(self, change_id: str, command: Any) -> DesignChange:
        before = self.get_change(change_id)
        gate = self.evaluate_command(change_id, command)
        if not gate["allowed"]:
            reason = gate["denial_reasons"][0]
            raise DesignLifecycleError(reason, reason.replace("_", " "),
                                       {"change_version": before.version})
        expected_version = getattr(command, "expected_version", None)
        if expected_version is not None and expected_version != before.version:
            raise DesignLifecycleError(
                "stale_preflight", "change version has advanced",
                {"expected_version": expected_version, "actual_version": before.version})
        if before.state in {ChangeState.CLOSED, ChangeState.ABANDONED}:
            raise DesignLifecycleError("terminal_change", "terminal change is immutable")
        now = self.clock()
        payload: dict[str, Any]
        name: str
        if isinstance(command, PlanChange):
            self._require_state(before, {ChangeState.OPEN, ChangeState.PLANNED})
            normalized_changes = tuple({
                **dict(claim),
                "authority": "DESIGN_ANNOTATION",
                "base_as_is_support": {
                    "canonical_revision": before.base_canonical_revision,
                    "support": list(claim.get("support", [])),
                },
                "design_intent": before.intent,
                "expected_change": dict(claim.get("expected_evidence", {})),
            } for claim in command.expected_changes)
            rev_payload = {
                "change_id": before.id,
                "architecture_proposal_ref": vars(
                    command.architecture_proposal_ref
                    or before.design_revision.architecture_proposal_ref)
                if (command.architecture_proposal_ref
                    or before.design_revision.architecture_proposal_ref) else None,
                "flow_model_ref": vars(
                    command.flow_model_ref or before.design_revision.flow_model_ref)
                if (command.flow_model_ref or before.design_revision.flow_model_ref)
                else None,
                "expected_changes": list(normalized_changes),
            }
            revision = DesignRevision(
                id=f"design-rev-{uuid.uuid4().hex}", change_id=before.id,
                authority="DESIGN_ANNOTATION",
                architecture_proposal_ref=(command.architecture_proposal_ref
                                           or before.design_revision.architecture_proposal_ref),
                flow_model_ref=(command.flow_model_ref
                                or before.design_revision.flow_model_ref),
                expected_changes=normalized_changes, created_at=now,
                digest=_digest(rev_payload),
            )
            drc = run_design_drc(revision.expected_changes, before.scope)
            after = replace(before, design_revision=revision, design_drc=drc,
                            state=ChangeState.PLANNED,
                            version=before.version + 1, updated_at=now)
            payload = {"design_revision": revision.id, "drc": drc}
            name = "plan"
        elif isinstance(command, DesignMutation):
            self._require_state(before, {ChangeState.OPEN, ChangeState.PLANNED})
            result, ref = self._apply_design_mutation(before, command)
            architecture_ref = before.design_revision.architecture_proposal_ref
            flow_ref = before.design_revision.flow_model_ref
            if command.plane == "architecture":
                architecture_ref = ref
                name = "architecture_mutation"
            elif command.plane == "flow":
                flow_ref = ref
                name = "flow_mutation"
            else:
                raise DesignLifecycleError(
                    "invalid_design_plane", "design plane must be architecture or flow",
                    {"plane": command.plane})
            rev_payload = {
                "change_id": before.id,
                "architecture_proposal_ref": vars(architecture_ref)
                if architecture_ref else None,
                "flow_model_ref": vars(flow_ref) if flow_ref else None,
                "expected_changes": list(before.design_revision.expected_changes),
            }
            revision = DesignRevision(
                id=f"design-rev-{uuid.uuid4().hex}", change_id=before.id,
                authority="DESIGN_ANNOTATION",
                architecture_proposal_ref=architecture_ref,
                flow_model_ref=flow_ref,
                expected_changes=before.design_revision.expected_changes,
                created_at=now, digest=_digest(rev_payload),
            )
            after = replace(before, design_revision=revision,
                            version=before.version + 1, updated_at=now)
            payload = {
                "design_revision": revision.id,
                "plane": command.plane,
                "operation": command.operation,
                "result": result,
            }
        elif isinstance(command, BeginImplementation):
            self._require_state(before, {ChangeState.PLANNED})
            if not before.design_drc or not before.design_drc.get("acceptable"):
                raise DesignLifecycleError(
                    "design_drc_not_acceptable",
                    "implementation cannot begin until design DRC is acceptable")
            implementation = {
                "expected_touched_scope": command.expected_touched_scope,
                "actual_changed_files": [], "actual_changed_symbols": [],
                "current_evidence_revision": before.base_canonical_revision,
                "actual_evidence": [],
                "observation_status": "previous",
            }
            after = replace(before, implementation=implementation,
                            state=ChangeState.IMPLEMENTING,
                            version=before.version + 1, updated_at=now)
            payload = implementation
            name = "begin_implementation"
        elif isinstance(command, RecordRepairApplication):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            implementation = dict(before.implementation or {})
            applications = list(implementation.get("repair_applications", []))
            applications.append({
                "application_id": command.application_id,
                "previous_attempt": command.previous_attempt,
                "repair_candidate_id": command.repair_candidate_id,
                "source_revision_after_claim": command.source_revision_after_claim,
                "status": "UNVERIFIED",
            })
            implementation["repair_applications"] = applications
            implementation["observation_status"] = "previous"
            after = replace(before, implementation=implementation,
                            version=before.version + 1, updated_at=now)
            payload = applications[-1]
            name = "record_repair_application"
        elif isinstance(command, RecordReindex):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            raise DesignLifecycleError(
                "index_job_proof_required",
                "record_reindex cannot advance ACTUAL without a successful linked index job;"
                " request and adopt the generic index job")
        elif isinstance(command, RequestReindex):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            prior = (before.implementation or {}).get("index_job") or {}
            if prior.get("job_id") and not prior.get("adopted_at"):
                old_job = self._index_job(prior["job_id"], before.repo_id)
                if old_job.get("status") != "failed":
                    raise DesignLifecycleError(
                        "index_job_already_requested", "prior index job is active")
            job = self._index_job(command.job_id, before.repo_id)
            if job.get("status") != "pending":
                raise DesignLifecycleError(
                    "index_job_not_pending", "index job must be pending at request")
            implementation = dict(before.implementation or {})
            implementation["observation_status"] = "pending"
            implementation["index_job"] = {
                "job_id": command.job_id,
                "requested_design_revision": before.design_revision.id,
                "input_canonical_revision": self._active_revision(before.repo_id),
                "requested_at": now,
            }
            after = replace(before, implementation=implementation,
                            version=before.version + 1, updated_at=now)
            payload = {"index_job": implementation["index_job"]}
            name = "request_reindex"
        elif isinstance(command, AdoptIndexJob):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            linked = (before.implementation or {}).get("index_job") or {}
            if linked.get("job_id") != command.job_id or (
                linked.get("requested_design_revision") != before.design_revision.id
            ):
                raise DesignLifecycleError(
                    "index_job_identity_mismatch", "job is not bound to current design")
            if linked.get("adopted_at"):
                raise DesignLifecycleError(
                    "index_job_already_adopted", "job receipt already adopted")
            job = self._index_job(command.job_id, before.repo_id)
            if job.get("status") != "done" or not isinstance(job.get("result"), dict):
                raise DesignLifecycleError(
                    "index_job_incomplete", "job has not completed successfully")
            result = job["result"]
            if result.get("repo_id") != before.repo_id or not result.get("run_id"):
                raise DesignLifecycleError(
                    "index_job_identity_mismatch", "job result lacks repo/run identity")
            revision = result.get("snapshot_id")
            snap = self.store.snapshot(revision) if revision else None
            if not snap or snap.get("repo_id") != before.repo_id or (
                snap.get("publication_status") != "published"
            ):
                raise DesignLifecycleError(
                    "index_job_result_unpublished", "job result is not published")
            if revision != self._active_revision(before.repo_id):
                raise DesignLifecycleError(
                    "index_job_result_stale", "job result is no longer current")
            if revision != linked.get("input_canonical_revision"):
                meta = snap.get("meta_json", snap.get("meta", {}))
                meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
                if (not result.get("run_id") or
                    meta.get("run_id") != result["run_id"] or
                    snap.get("parent_id") != linked.get("input_canonical_revision")):
                    raise DesignLifecycleError(
                        "index_job_run_mismatch",
                        "published revision is not the linked job's output")
            implementation = self._implementation_from_index_result(
                before, result, command.actual_evidence_selectors)
            implementation["index_job"] = {**linked, "adopted_at": now}
            after = replace(before, implementation=implementation,
                            version=before.version + 1, updated_at=now)
            payload = implementation
            name = "adopt_index_job"
        elif isinstance(command, RunReindex):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            raise DesignLifecycleError(
                "index_job_proof_required",
                "run_reindex requires a persistent generic job and adoption receipt")
        elif isinstance(command, EvaluateExpectedActual):
            self._require_state(before, {ChangeState.IMPLEMENTING})
            if not before.implementation or (
                before.implementation.get("observation_status") != "recorded"
            ):
                raise DesignLifecycleError("reindex_required", "record re-index first")
            self._require_actual_current(before)
            expected_actual = self._evaluate(before)
            lvs = self.lvs_runner(before)
            matched = (expected_actual["outcome"] == "MATCHED"
                       and lvs.get("overall_status") == "MATCH")
            after = replace(
                before, expected_actual=expected_actual, lvs_result=lvs,
                state=(ChangeState.EVIDENCE_MATCHED if matched
                       else ChangeState.IMPLEMENTING),
                version=before.version + 1, updated_at=now,
            )
            payload = {"expected_actual": expected_actual, "lvs": lvs}
            name = "evaluate_expected_actual"
        elif isinstance(command, VerifyChange):
            self._require_state(before, {ChangeState.EVIDENCE_MATCHED})
            self._require_actual_current(before)
            by_id = {str(x.get("criterion_id")): dict(x) for x in command.results}
            normalized = []
            missing = []
            for criterion in before.acceptance_criteria:
                result = by_id.get(criterion.id, {
                    "criterion_id": criterion.id, "status": "NOT_VERIFIED",
                    "evidence_refs": [],
                })
                normalized.append(result)
                if criterion.required and (
                    result.get("status") != "PASS"
                    or not result.get("evidence_refs")):
                    missing.append(criterion.id)
            if missing:
                raise DesignLifecycleError(
                    "verification_incomplete", "required verification is absent",
                    {"criteria": missing, "status": "NOT_VERIFIED"})
            test_receipts = {r["id"]: r for r in self.repo.independent_receipts(
                "test", before.id)}
            for criterion in before.acceptance_criteria:
                if not criterion.required or criterion.kind != "test":
                    continue
                evidence_refs = by_id.get(criterion.id, {}).get("evidence_refs", [])
                if not any((ref in test_receipts and
                            test_receipts[ref]["result"] == "PASS" and
                            test_receipts[ref]["design_revision"] == before.design_revision.id and
                            test_receipts[ref]["canonical_revision"] ==
                            before.implementation["current_evidence_revision"])
                           for ref in evidence_refs):
                    raise DesignLifecycleError(
                        "test_receipt_required", "required test needs independent"
                        " run proof", {"criterion_id": criterion.id})
            after = replace(before, verification=tuple(normalized),
                            state=ChangeState.VERIFIED,
                            version=before.version + 1, updated_at=now)
            payload = {"results": normalized}
            name = "verify"
        elif isinstance(command, CloseChange):
            self._require_state(before, {ChangeState.VERIFIED})
            self._require_actual_current(before)
            self._assert_closable(before)
            final_revision = before.implementation["current_evidence_revision"]
            close_payload = {
                "base_revision": before.base_canonical_revision,
                "design_revision": before.design_revision.id,
                "final_canonical_revision": final_revision,
                "expected_actual_diff": before.expected_actual,
                "verification_evidence": list(before.verification),
                "remaining_unknowns": list(before.remaining_unknowns),
                "rule_versions": dict(command.rule_versions),
            }
            close_payload["digest"] = _digest(close_payload)
            after = replace(before, close_receipt=close_payload,
                            state=ChangeState.CLOSED,
                            version=before.version + 1, updated_at=now)
            payload = close_payload
            name = "close"
        elif isinstance(command, AbandonChange):
            after = replace(before, state=ChangeState.ABANDONED,
                            version=before.version + 1, updated_at=now)
            payload = {"reason": command.reason}
            name = "abandon"
        else:
            raise DesignLifecycleError(
                "unsupported_command", "unsupported lifecycle command",
                {"change_id": change_id, "command": type(command).__name__})
        receipt = self._receipt(
            after, before.version + 1, name, command.actor,
            before.state.value, payload)
        self.repo.update(before.version, after, receipt)
        return after

    def _apply_design_mutation(
            self, change: DesignChange, command: DesignMutation,
    ) -> tuple[dict[str, Any], DesignRef]:
        try:
            if command.plane == "architecture":
                return self._apply_architecture_mutation(change, command)
            if command.plane == "flow":
                return self._apply_flow_mutation(change, command)
        except DesignLifecycleError:
            raise
        except Exception as exc:  # domain stores expose typed errors per backend
            code = str(getattr(exc, "code", "design_mutation_failed"))
            details = dict(getattr(exc, "details", {}) or {})
            raise DesignLifecycleError(code, str(getattr(exc, "message", exc)),
                                       details) from exc
        raise DesignLifecycleError(
            "invalid_design_plane", "design plane must be architecture or flow",
            {"plane": command.plane})

    def _apply_architecture_mutation(
            self, change: DesignChange, command: DesignMutation,
    ) -> tuple[dict[str, Any], DesignRef]:
        p = dict(command.payload)
        op = command.operation
        if op == "create_workspace":
            if p.get("repo_id") != change.repo_id:
                raise DesignLifecycleError(
                    "design_repo_mismatch", "mutation repository differs from change",
                    {"change_repo_id": change.repo_id, "repo_id": p.get("repo_id")})
            if not self.store.repo(change.repo_id):
                raise DesignLifecycleError("repo_not_found", "repository not found",
                                           {"repo_id": change.repo_id})
            workspace = self.store.arch_create_workspace(
                p["repo_id"], p["name"], p.get("description", ""))
            workspace_id = workspace["id"]
            model = self.store.arch_models(workspace_id)[0]
            identity = model["id"]
            result = {"workspace": workspace, "model": model}
        else:
            wid = str(p.get("workspace_id") or "")
            workspace = self.store.arch_workspace(wid)
            if not workspace:
                raise DesignLifecycleError("workspace_not_found", "workspace not found",
                                           {"workspace_id": wid})
            if workspace["repo_id"] != change.repo_id:
                raise DesignLifecycleError(
                    "design_repo_mismatch", "workspace differs from change repository")
            workspace_id = wid
            identity = str(p.get("model_id") or "")
            if op == "fork_proposal":
                source = p.get("source_model_id")
                if not source:
                    source = next((m["id"] for m in self.store.arch_models(wid)
                                   if m["kind"] == "as_is"), None)
                if not source:
                    raise DesignLifecycleError("model_not_found", "AS-IS model not found")
                result = self.store.arch_fork_model(
                    wid, source, p["name"], p.get("description", ""))
                identity = result["id"]
            elif op == "create_component":
                self._require_arch_model(wid, p["model_id"])
                self._validate_arch_parent(wid, p["model_id"], p.get("parent_id"))
                result = self.store.arch_create_component(
                    wid, p["model_id"], p["kind"], p["name"],
                    p.get("description", ""), p.get("parent_id"))
            elif op == "batch_component":
                entities = [tuple(x) for x in p.get("entities", [])]
                self._require_arch_model(wid, p["model_id"])
                self._validate_arch_parent(wid, p["model_id"], p.get("parent_id"))
                self._validate_canonical_entities(change.repo_id, entities)
                result = self.store.arch_batch_component(
                    wid, p["model_id"], p["kind"], p["name"],
                    p.get("description", ""), p.get("parent_id"), entities)
            elif op == "update_component":
                component = self.store.arch_component(
                    wid, p["component_id"], p["model_id"])
                if not component:
                    raise DesignLifecycleError("component_not_found", "component not found")
                if p.get("parent_id") is not None:
                    self._validate_arch_parent(wid, p["model_id"], p["parent_id"])
                    self._assert_no_arch_cycle(
                        wid, p["model_id"], p["component_id"], p["parent_id"])
                result = self.store.arch_update_component(
                    wid, p["model_id"], p["component_id"],
                    kind=p.get("kind"), name=p.get("name"),
                    description=p.get("description"), parent_id=p.get("parent_id"),
                    clear_parent=bool(p.get("clear_parent", False)))
            elif op == "delete_component":
                if not self.store.arch_component(wid, p["component_id"], p["model_id"]):
                    raise DesignLifecycleError("component_not_found", "component not found")
                result = self.store.arch_delete_component(
                    wid, p["model_id"], p["component_id"],
                    subtree=bool(p.get("subtree", False)))
            elif op == "create_relation":
                self._require_arch_model(wid, p["model_id"])
                self._validate_relation_endpoints(
                    wid, p["model_id"], p["src_id"], p["dst_id"])
                if p["src_id"] == p["dst_id"]:
                    raise DesignLifecycleError(
                        "relation_self_loop", "a relation cannot connect a component to itself")
                if any((r["kind"], r["src_id"], r["dst_id"]) ==
                       (p["kind"], p["src_id"], p["dst_id"])
                       for r in self.store.arch_relations(wid, p["model_id"])):
                    raise DesignLifecycleError("relation_exists",
                                               "an identical relation already exists")
                result = self.store.arch_create_relation(
                    wid, p["model_id"], p["kind"], p["src_id"], p["dst_id"],
                    p.get("label"))
            elif op == "update_relation":
                if not self.store.arch_relation(wid, p["relation_id"], p["model_id"]):
                    raise DesignLifecycleError("relation_not_found", "relation not found")
                result = self.store.arch_update_relation(
                    wid, p["model_id"], p["relation_id"], kind=p.get("kind"),
                    label=p.get("label"), clear_label=bool(p.get("clear_label", False)))
            elif op == "delete_relation":
                self.store.arch_delete_relation(wid, p["relation_id"])
                result = {"deleted": p["relation_id"]}
            elif op in {"create_mapping", "batch_mappings"}:
                entities = [tuple(x) for x in p.get("entities", [])]
                component = self.store.arch_component(wid, p["component_id"])
                if not component:
                    raise DesignLifecycleError("component_not_found", "component not found")
                identity = component["model_id"]
                for entity_type, entity_id, _note in entities:
                    expected = "node" if str(entity_id).startswith("node:") else (
                        "edge" if str(entity_id).startswith("edge:") else None)
                    if expected is None or expected != entity_type:
                        raise DesignLifecycleError(
                            "invalid_entity", "mapping target is not a canonical evidence id",
                            {"entity_id": entity_id, "entity_type": entity_type})
                    if self.store.arch_mapping_exists(
                            wid, p["component_id"], entity_type, entity_id):
                        raise DesignLifecycleError("mapping_exists",
                                                   "mapping already exists")
                if op == "batch_mappings":
                    self._validate_canonical_entities(change.repo_id, entities)
                result = {"mappings": self.store.arch_batch_mappings(
                    wid, p["component_id"], entities)}
            elif op == "delete_mapping":
                mapping_model = next((m["id"] for m in self.store.arch_models(wid)
                                      if any(x["id"] == p["mapping_id"]
                                             for x in self.store.arch_mappings(
                                                 wid, m["id"]))), None)
                if mapping_model is None:
                    raise DesignLifecycleError("mapping_not_found", "mapping not found")
                identity = mapping_model
                self.store.arch_delete_mapping(wid, p["mapping_id"])
                result = {"deleted": p["mapping_id"]}
            elif op == "put_layout":
                updated = self.store.arch_put_layout(
                    wid, p["model_id"], p["layout"], p.get("updated_at"))
                result = {"model_id": p["model_id"], "updated_at": updated}
            else:
                raise DesignLifecycleError(
                    "unsupported_design_mutation", "unsupported architecture mutation",
                    {"operation": op})
        state = self._architecture_state(workspace_id, identity)
        return dict(result), DesignRef(identity=identity, revision=_digest(state))

    def _require_arch_model(self, workspace_id: str, model_id: str) -> dict:
        model = self.store.arch_model(workspace_id, model_id)
        if not model:
            raise DesignLifecycleError("model_not_found", "architecture model not found",
                                       {"model_id": model_id})
        return model

    def _validate_arch_parent(self, workspace_id: str, model_id: str,
                              parent_id: str | None) -> None:
        if parent_id is None:
            return
        if self.store.arch_component(workspace_id, parent_id, model_id):
            return
        if self.store.arch_component(workspace_id, parent_id):
            raise DesignLifecycleError(
                "cross_model_reference", "parent belongs to a different model")
        raise DesignLifecycleError("component_not_found", "parent component not found")

    def _assert_no_arch_cycle(self, workspace_id: str, model_id: str,
                              component_id: str, parent_id: str) -> None:
        node = self.store.arch_component(workspace_id, parent_id, model_id)
        seen: set[str] = set()
        while node is not None:
            if node["id"] == component_id:
                raise DesignLifecycleError(
                    "hierarchy_cycle", "parent change would create a hierarchy cycle")
            if node["id"] in seen:
                break
            seen.add(node["id"])
            node = (self.store.arch_component(workspace_id, node["parent_id"], model_id)
                    if node.get("parent_id") else None)

    def _validate_relation_endpoints(self, workspace_id: str, model_id: str,
                                     source: str, target: str) -> None:
        for component_id in (source, target):
            if self.store.arch_component(workspace_id, component_id, model_id):
                continue
            if self.store.arch_component(workspace_id, component_id):
                raise DesignLifecycleError(
                    "cross_model_reference", "relation endpoint belongs to another model")
            raise DesignLifecycleError("component_not_found", "relation endpoint not found")

    def _validate_canonical_entities(
            self, repo_id: str, entities: list[tuple[Any, ...]]) -> None:
        snapshot_id = self.store.current_snapshot(repo_id)
        invalid = []
        for entity_type, entity_id, *_rest in entities:
            if not snapshot_id or not self.store.entity_exists(
                    repo_id, snapshot_id, str(entity_type), str(entity_id)):
                invalid.append(str(entity_id))
        if invalid:
            raise DesignLifecycleError(
                "invalid_entity", "canonical mapping entities are absent",
                {"entity_ids": invalid, "snapshot_id": snapshot_id})

    def _architecture_state(self, workspace_id: str,
                            model_id: str) -> dict[str, Any]:
        model = self._require_arch_model(workspace_id, model_id)
        return {
            "workspace": self.store.arch_workspace(workspace_id),
            "model": model,
            "components": self.store.arch_components(workspace_id, model_id),
            "relations": self.store.arch_relations(workspace_id, model_id),
            "mappings": self.store.arch_mappings(workspace_id, model_id),
            "layout": self.store.arch_layout(workspace_id, model_id),
        }

    def _apply_flow_mutation(
            self, change: DesignChange, command: DesignMutation,
    ) -> tuple[dict[str, Any], DesignRef]:
        from ..flow.projection import FlowProjectionService
        from ..flow.service import FlowService
        p = dict(command.payload)
        op = command.operation
        svc = FlowService._for_design_lifecycle(self.store)
        projection = FlowProjectionService._for_design_lifecycle(self.store)
        if op == "create_blank":
            if p.get("repo_id") != change.repo_id:
                raise DesignLifecycleError("design_repo_mismatch",
                                           "mutation repository differs from change")
            sid = p.get("snapshot_id") or self.store.current_snapshot(change.repo_id)
            result = svc.create_flow(change.repo_id, p.get("name") or "Untitled Topology", sid)
            flow_id = result["id"]
            result = svc.get_flow(flow_id)
        elif op == "from_symbol":
            if p.get("repo_id") != change.repo_id:
                raise DesignLifecycleError("design_repo_mismatch",
                                           "mutation repository differs from change")
            sid = p.get("snapshot_id") or self.store.current_snapshot(change.repo_id)
            result = projection.from_symbol(
                change.repo_id, sid, p["symbol"], name=p.get("name"),
                include_callees=bool(p.get("include_callees", True)),
                workspace_id=p.get("workspace_id"),
                architecture_model_id=p.get("architecture_model_id"))
            flow_id = result["flow"]["id"]
        elif op == "from_component":
            result = projection.from_component(
                p["workspace_id"], p["model_id"], p["component_id"],
                name=p.get("name"))
            flow_id = result["flow"]["id"]
        else:
            flow_id = str(p.get("flow_id") or "")
            flow = svc.require_flow(flow_id)
            if flow["repo_id"] != change.repo_id:
                raise DesignLifecycleError("design_repo_mismatch",
                                           "flow differs from change repository")
            if op == "update_flow":
                result = svc.update_flow(flow_id, name=p.get("name"),
                                         layout=p.get("layout"), meta=p.get("meta"))
            elif op == "record_agent_action":
                result = svc.record_agent_action(flow_id, p["action"])
            elif op == "add_block":
                result = svc.add_block(
                    flow_id, p.get("kind", "proposed"), p["name"],
                    state=p.get("state", "proposed"),
                    parent_block_id=p.get("parent_block_id"), code=p.get("code"),
                    meta=p.get("meta"))
            elif op == "update_block":
                result = svc.update_block(
                    flow_id, p["block_id"], name=p.get("name"), code=p.get("code"),
                    state=p.get("state"), kind=p.get("kind"),
                    parent_block_id=p.get("parent_block_id"),
                    clear_parent=bool(p.get("clear_parent", False)), meta=p.get("meta"))
            elif op == "delete_block":
                result = svc.delete_block(flow_id, p["block_id"])
            elif op == "add_port":
                result = svc.add_port(
                    flow_id, block_id=p["block_id"], name=p["name"],
                    direction=p["direction"], semantic_kind=p["semantic_kind"],
                    code_type=p.get("code_type"),
                    position_order=int(p.get("position_order", 0)), meta=p.get("meta"))
            elif op == "update_port":
                result = svc.update_port(
                    flow_id, p["port_id"], name=p.get("name"),
                    semantic_kind=p.get("semantic_kind"), code_type=p.get("code_type"),
                    clear_type=bool(p.get("clear_type", False)),
                    position_order=p.get("position_order"), meta=p.get("meta"))
            elif op == "delete_port":
                result = svc.delete_port(flow_id, p["port_id"])
            elif op == "add_net":
                result = svc.add_net(
                    flow_id, source_port_id=p["source_port_id"],
                    target_port_id=p["target_port_id"], kind=p.get("kind", "control"),
                    label=p.get("label"), meta=p.get("meta"))
            elif op == "update_net":
                result = svc.update_net(
                    flow_id, p["net_id"], kind=p.get("kind"), label=p.get("label"),
                    meta=p.get("meta"), clear_label=bool(p.get("clear_label", False)))
            elif op == "delete_net":
                result = svc.delete_net(flow_id, p["net_id"])
            elif op == "create_composite":
                result = svc.create_composite(flow_id, p["name"], p["block_ids"])
            elif op == "expand":
                result = projection.expand(flow_id)
            else:
                raise DesignLifecycleError(
                    "unsupported_design_mutation", "unsupported flow mutation",
                    {"operation": op})
        return dict(result), DesignRef(
            identity=flow_id, revision=self._flow_design_digest(flow_id))

    def _flow_design_digest(self, flow_id: str) -> str:
        """Bind stored TO-BE state, never live source-code DTO enrichment."""
        from ..flow.service import FlowService
        payload = {
            "flow": FlowService(self.store).require_flow(flow_id),
            "blocks": self.store.flow_blocks(flow_id),
            "ports": self.store.flow_ports(flow_id),
            "nets": self.store.flow_nets(flow_id),
            "bindings": self.store.flow_bindings(flow_id),
            "layout": self.store.flow_layout(flow_id),
        }
        return "flow-design-v1:" + _digest(payload)

    def get_change(self, change_id: str) -> DesignChange:
        change = self.repo.get(change_id)
        if change is None:
            raise DesignLifecycleError(
                "change_not_found", "design change not found", {"change_id": change_id})
        return change

    def list_changes(self, *, repo_id: str | None = None) -> list[DesignChange]:
        return self.repo.list(repo_id)

    def receipts(self, change_id: str) -> list[LifecycleReceipt]:
        self.get_change(change_id)
        return self.repo.receipts(change_id)

    def get_design_revision(self, change_id: str,
                            revision_id: str) -> dict[str, Any]:
        self.get_change(change_id)
        revision = self.repo.revision(change_id, revision_id)
        if revision is None:
            raise DesignLifecycleError(
                "design_revision_not_found", "design revision not found",
                {"change_id": change_id, "revision_id": revision_id})
        return revision

    def preflight(self, change_id: str,
                  authorization_identity: str | None = None) -> dict[str, Any]:
        """Read-only view of the same state and close gates used by commands.

        Approval is denied without an injected authenticated identity adapter.
        """
        change = self.get_change(change_id)
        actions: dict[str, dict[str, Any]] = {}
        states = {
            "plan": {ChangeState.OPEN, ChangeState.PLANNED},
            "design_mutation": {ChangeState.OPEN, ChangeState.PLANNED},
            "begin_implementation": {ChangeState.PLANNED},
            "run_reindex": {ChangeState.IMPLEMENTING},
            "request_reindex": {ChangeState.IMPLEMENTING},
            "adopt_index_job": {ChangeState.IMPLEMENTING},
            "record_reindex": {ChangeState.IMPLEMENTING},
            "evaluate_expected_actual": {ChangeState.IMPLEMENTING},
            "verify": {ChangeState.EVIDENCE_MATCHED},
            "close": {ChangeState.VERIFIED},
        }
        for name, allowed_states in states.items():
            if change.state in {ChangeState.CLOSED, ChangeState.ABANDONED}:
                actions[name] = {
                    "allowed": False,
                    "denial_reasons": ["terminal_change"],
                    "required_evidence": [],
                }
                continue
            reasons: list[str] = []
            try:
                self._require_state(change, allowed_states)
                if name in {"record_reindex", "run_reindex"}:
                    reasons.append("index_job_proof_required")
                if name in {"run_reindex", "record_reindex"}:
                    self._require_no_active_job(change)
                if name == "begin_implementation" and not (
                    change.design_drc and change.design_drc.get("acceptable")
                ):
                    reasons.append("design_drc_not_acceptable")
                if name == "evaluate_expected_actual" and (
                    not change.implementation or
                    change.implementation.get("observation_status") != "recorded"
                ):
                    reasons.append("reindex_required")
                if name == "verify" and any(
                    c.required and c.kind == "test" for c in change.acceptance_criteria
                ) and not any(
                    r.get("result") == "PASS" and
                    r.get("design_revision") == change.design_revision.id and
                    r.get("canonical_revision") == (change.implementation or {}).get(
                        "current_evidence_revision")
                    for r in self.repo.independent_receipts("test", change.id)
                ):
                    reasons.append("test_receipt_required")
                if name in {"evaluate_expected_actual", "verify", "close"} and (
                    (change.implementation or {}).get("observation_status") == "recorded"
                ):
                    try:
                        self._require_actual_current(change)
                    except DesignLifecycleError as exc:
                        reasons.append(exc.code)
                if name == "request_reindex" and (
                    (change.implementation or {}).get("index_job", {}).get("job_id")
                    and not (change.implementation or {}).get("index_job", {}).get("adopted_at")
                ):
                    prior_id = change.implementation["index_job"]["job_id"]
                    prior_job = self.job_lookup(prior_id) if self.job_lookup else None
                    if not prior_job or prior_job.get("status") != "failed":
                        reasons.append("index_job_already_requested")
                if name == "adopt_index_job":
                    linked = (change.implementation or {}).get("index_job") or {}
                    if not linked.get("job_id"):
                        reasons.append("index_job_not_requested")
                    elif linked.get("adopted_at"):
                        reasons.append("index_job_already_adopted")
                    elif self.job_lookup:
                        job = self.job_lookup(linked["job_id"])
                        if not job or job.get("status") != "done":
                            reasons.append("index_job_incomplete")
                        elif (job.get("result") or {}).get("snapshot_id") != (
                            self._active_revision(change.repo_id)
                        ):
                            reasons.append("index_job_result_stale")
                    else:
                        reasons.append("index_job_unavailable")
                if name == "close":
                    try:
                        self._assert_closable(change)
                    except DesignLifecycleError as exc:
                        reasons.extend(exc.details.get("failed", [exc.code]))
            except DesignLifecycleError as exc:
                reasons.append(exc.code)
            actions[name] = {
                "allowed": not reasons,
                "denial_reasons": reasons,
                "required_evidence": ([c.id for c in change.acceptance_criteria
                                       if c.required] if name == "verify" else []),
            }
        auth = self._approval_authorization(change, authorization_identity)
        terminal = change.state in {ChangeState.CLOSED, ChangeState.ABANDONED}
        actions["approve"] = {
            "allowed": bool(auth) and not terminal,
            "denial_reasons": (["terminal_change"] if terminal else
                               [] if auth else ["approver_authorization_unavailable"]),
            "required_evidence": [],
        }
        actions["abandon"] = {
            "allowed": change.state not in {ChangeState.CLOSED, ChangeState.ABANDONED},
            "denial_reasons": ([] if change.state not in {
                ChangeState.CLOSED, ChangeState.ABANDONED} else ["terminal_change"]),
            "required_evidence": [],
        }
        return {
            "change_id": change.id, "change_version": change.version,
            "policy_version": "design-lifecycle-gates/2",
            "versionless_compatibility": "NO_REUSABLE_PREFLIGHT_GUARANTEE",
            "subject_binding": self._result_binding(change),
            "authorization": "VERIFIED" if auth else "UNAVAILABLE",
            "authorization_requirement": "approve_design",
            "authorization_identity": auth["authorization_id"] if auth else None,
            "approver_identity": auth["subject"] if auth else None,
            "allowed_actions": [k for k, v in actions.items() if v["allowed"]],
            "denied_actions": [k for k, v in actions.items() if not v["allowed"]],
            "actions": actions,
        }

    def evaluate_command(self, change_id: str, command: Any) -> dict[str, Any]:
        """Read-only command gate used by both callers and enforcement."""
        change = self.get_change(change_id)
        expected_version = getattr(command, "expected_version", None)
        if expected_version is not None and expected_version != change.version:
            reasons = ["stale_preflight"]
        elif change.state in {ChangeState.CLOSED, ChangeState.ABANDONED}:
            reasons = ["terminal_change"]
        else:
            names = {
                PlanChange: "plan", DesignMutation: "design_mutation",
                BeginImplementation: "begin_implementation",
                RecordReindex: "record_reindex", RunReindex: "run_reindex",
                RequestReindex: "request_reindex",
                AdoptIndexJob: "adopt_index_job",
                EvaluateExpectedActual: "evaluate_expected_actual",
                VerifyChange: "verify", CloseChange: "close",
                AbandonChange: "abandon",
            }
            name = names.get(type(command))
            if isinstance(command, RecordRepairApplication):
                reasons = ([] if change.state is ChangeState.IMPLEMENTING
                           else ["invalid_state"])
            elif name is None:
                reasons = ["unsupported_command"]
            else:
                reasons = list(self.preflight(change_id)["actions"][name][
                    "denial_reasons"])
                if (isinstance(command, VerifyChange) and
                        change.state is ChangeState.EVIDENCE_MATCHED):
                    by_id = {str(x.get("criterion_id")): x
                             for x in command.results}
                    if any(c.required and (
                        by_id.get(c.id, {}).get("status") != "PASS" or
                        not by_id.get(c.id, {}).get("evidence_refs"))
                           for c in change.acceptance_criteria):
                        reasons.insert(0, "verification_incomplete")
                if isinstance(command, RequestReindex) and not reasons:
                    try:
                        job = self._index_job(command.job_id, change.repo_id)
                        if job.get("status") != "pending":
                            reasons.append("index_job_not_pending")
                    except DesignLifecycleError as exc:
                        reasons.append(exc.code)
        return {
            "allowed": not reasons, "denial_reasons": reasons,
            "change_id": change.id, "change_version": change.version,
            "policy_version": "design-lifecycle-gates/2",
            "version_bound": expected_version is not None,
            "versionless_compatibility": (None if expected_version is not None
                                          else "NO_REUSABLE_PREFLIGHT_GUARANTEE"),
        }

    @staticmethod
    def _result_binding(change: DesignChange) -> dict[str, Any]:
        implementation = change.implementation or {}
        return {
            "baseline_revision": change.base_canonical_revision,
            "design_revision": change.design_revision.id,
            "actual_revision": implementation.get("current_evidence_revision"),
            "scope_digest": _digest(change.scope),
            "scope": change.scope,
        }

    def _build_brief(self, repo_id: str, snapshot_id: str,
                     scope: dict[str, Any]) -> EvidenceBrief:
        paths = set(scope.get("files", []))
        symbols = set(scope.get("symbols", []))
        rows = self.store.all_nodes(repo_id, snapshot_id)
        selected = [row for row in rows if (
            (not paths and not symbols)
            or row.get("path") in paths
            or row.get("id") in symbols
            or row.get("qname") in symbols
            or row.get("name") in symbols
        )]
        bounded = selected[:200]
        facts = tuple({
            "id": row.get("id"), "kind": row.get("kind"),
            "name": row.get("name"), "qname": row.get("qname"),
            "path": row.get("path"),
            "source_span": project_source_span(
                row, revision=snapshot_id, subject=str(row.get("id") or ""),
                entity_type="node"),
            "authority": "CANONICAL_CANDIDATE",
            "truth_class": "UNKNOWN", "coverage": "UNKNOWN",
            "resolution": "UNKNOWN", "execution_modality": "UNKNOWN",
            "provider": [],
        } for row in bounded)
        modules = tuple(x for x in facts if x["kind"] in {
            "MODULE", "PACKAGE", "NAMESPACE", "FILE"})
        data_interfaces = tuple(x for x in facts if x["kind"] in {
            "INTERFACE", "TYPE", "CLASS", "STRUCT", "PARAMETER"})
        flow_rows: list[dict[str, Any]] = []
        try:
            for flow in self.store.flow_models(repo_id):
                if flow.get("snapshot_id") != snapshot_id:
                    continue
                scope_symbol = flow.get("scope_symbol_id")
                if symbols and scope_symbol and scope_symbol not in symbols:
                    continue
                flow_rows.append({
                    "id": flow.get("id"), "name": flow.get("name"),
                    "snapshot_id": flow.get("snapshot_id"),
                    "scope_symbol_id": scope_symbol,
                    "authority": "DESIGN_ANNOTATION",
                    "truth_class": "UNKNOWN", "coverage": "PARTIAL",
                })
        except (AttributeError, NotImplementedError):
            flow_rows = []
        runtime = tuple(
            dict(x) for x in self.runtime_provider(repo_id, snapshot_id, scope))
        reference = tuple(
            dict(x) for x in self.reference_provider(repo_id, snapshot_id, scope))
        constraints: tuple[dict[str, Any], ...] = ({
            "kind": "bounded_brief", "selected": len(bounded),
            "available": len(selected), "limit": 200,
            "authority": "DESIGN_ANNOTATION",
        },)
        unknown_items: list[dict[str, Any]] = []
        if len(selected) > 200:
            unknown_items.append({
                "kind": "brief_truncated", "omitted": len(selected) - 200,
                "truth_class": "UNKNOWN", "coverage": "PARTIAL"})
        if not runtime:
            unknown_items.append({
                "kind": "runtime_evidence_unavailable",
                "truth_class": "UNKNOWN", "coverage": "UNKNOWN",
                "authority": "REFERENCE_EVIDENCE"})
        return EvidenceBrief(
            canonical_revision=snapshot_id, modules=modules, symbols=facts,
            flows=tuple(flow_rows), data_interfaces=data_interfaces,
            runtime_evidence=runtime, unknowns=tuple(unknown_items),
            reference_evidence=reference,
            constraints=constraints,
        )

    def _receipt(self, change: DesignChange, sequence: int, command: str,
                 actor: str, state_before: str | None,
                 payload: dict[str, Any]) -> LifecycleReceipt:
        receipt_id = f"receipt-{uuid.uuid4().hex}"
        payload = {**payload, "result_binding": {
            **self._result_binding(change),
            "run_identity": receipt_id,
            "rule_version": (payload.get("drc") or {}).get("rule_version")
            or (payload.get("expected_actual") or {}).get("rule_version")
            or payload.get("rule_versions") or "design-lifecycle/1",
        }}
        raw = {
            "change_id": change.id, "sequence": sequence, "command": command,
            "actor": actor, "state_before": state_before,
            "state_after": change.state.value, "payload": payload,
        }
        return LifecycleReceipt(
            id=receipt_id, change_id=change.id,
            sequence=sequence, command=command, actor=actor,
            state_before=state_before, state_after=change.state.value,
            payload=payload, digest=_digest(raw), created_at=self.clock(),
        )

    @staticmethod
    def _require_state(change: DesignChange, allowed: set[ChangeState]) -> None:
        if change.state not in allowed:
            raise DesignLifecycleError(
                "invalid_transition", f"command invalid from {change.state.value}",
                {"state": change.state.value,
                 "allowed": sorted(x.value for x in allowed)})

    @staticmethod
    def _fact_key(fact: dict[str, Any]) -> tuple[str, str, str]:
        return (str(fact.get("kind") or ""),
                str(fact.get("source") or fact.get("symbol") or ""),
                str(fact.get("target") or ""))

    def _evaluate(self, change: DesignChange) -> dict[str, Any]:
        expected = [dict(x.get("expected_evidence", {}))
                    for x in change.design_revision.expected_changes
                    if x.get("expected_evidence")]
        revision = change.implementation["current_evidence_revision"]
        # The caller's actual_evidence list is only a selector, never a
        # searched domain. Search the published graph at the pinned revision.
        kinds = {str(x.get("kind") or "") for x in expected}
        sources = {str(x.get("source") or x.get("symbol") or "")
                   for x in expected}
        selected = [dict(x) for x in change.implementation.get(
            "actual_evidence", [])]
        kinds.update(str(x.get("kind") or "") for x in selected)
        sources.update(str(x.get("source") or x.get("symbol") or "")
                       for x in selected)
        scoped_paths = set(change.scope.get("files", []))
        scoped_symbols = set(change.scope.get("symbols", []))
        for node in self.store.all_nodes(change.repo_id, revision):
            if (node.get("path") in scoped_paths or
                node.get("id") in scoped_symbols or
                node.get("qname") in scoped_symbols or
                node.get("name") in scoped_symbols):
                sources.add(str(node["id"]))
        if not kinds and sources:
            # CALLS is the minimum comparison vocabulary for a scoped
            # lifecycle change; callers cannot shrink it by omitting selectors.
            kinds.add("CALLS")
        kinds.discard("")
        sources.discard("")
        graph_rows = [row for row in self.store.all_edges(change.repo_id, revision)
                      if row.get("kind") in kinds and row.get("src_id") in sources]
        actual = [{"id": row["id"], "kind": row["kind"],
                   "source": row["src_id"], "target": row["dst_id"],
                   "canonical_revision": revision} for row in graph_rows]
        base_keys = {(row["kind"], row["src_id"], row["dst_id"])
                     for row in self.store.all_edges(
                         change.repo_id, change.base_canonical_revision)}
        expected_by = {self._fact_key(x): x for x in expected}
        actual_by = {self._fact_key(x): x for x in actual}
        missing_keys = sorted(set(expected_by) - set(actual_by))
        unexpected_keys = sorted(
            (set(actual_by) - set(expected_by)) - base_keys)
        certificates: dict[tuple[str, str, str], dict[str, Any]] = {}
        applicability: dict[tuple[str, str, str], tuple[bool, str]] = {}
        from rintel.coverage_authority import applicable_complete
        for key in missing_keys:
            certs = self.repo.coverage_certificates(
                change.repo_id, revision, key[1], "DIRECT_STATIC_CALL") \
                if expected_by[key].get("call_semantics") == "DIRECT_STATIC_CALL" \
                else []
            if certs:
                certificates[key] = certs[0]
                applicability[key] = applicable_complete(
                    certs[0], repo_id=change.repo_id, revision=revision,
                    expected=expected_by[key], store=self.store)
            else:
                applicability[key] = (False, "no_exact_direct_call_certificate")
        proved_missing = [key for key in missing_keys if applicability[key][0]]
        unknown_missing = sorted(set(missing_keys) - set(proved_missing))
        negative_coverage = ("COMPLETE" if missing_keys and not unknown_missing
                             else "UNKNOWN" if missing_keys else "NOT_REQUIRED")
        if unknown_missing:
            outcome = "UNKNOWN"
        elif proved_missing:
            outcome = "MISSING_IMPLEMENTATION"
        elif unexpected_keys:
            outcome = "UNEXPECTED_IMPLEMENTATION"
        else:
            outcome = "MATCHED"
        comparison_identity = _digest({
            "baseline": change.base_canonical_revision,
            "design": change.design_revision.id, "actual": revision,
            "scope": change.scope, "expected": expected,
            "observed": sorted(actual_by),
        })
        return {
            "comparison_identity": comparison_identity,
            "rule_version": "design-lifecycle-compare/2",
            "outcome": outcome,
            "expected_evidence": expected,
            "actual_canonical_evidence": actual,
            "matched": [actual_by[k] for k in sorted(set(expected_by) & set(actual_by))],
            "missing": [expected_by[k] for k in proved_missing],
            "unproven_absence": [expected_by[k] for k in unknown_missing],
            "unexpected": [actual_by[k] for k in unexpected_keys],
            "searched_domain": {
                "repo_id": change.repo_id, "canonical_revision": revision,
                "source_ids": sorted(sources), "edge_kinds": sorted(kinds),
                "scope": change.scope,
            },
            "negative_coverage": negative_coverage,
            "coverage_certificate_refs": [certificates[k]["id"]
                                          for k in sorted(certificates)],
            "coverage_certificates": [certificates[k]
                                      for k in sorted(certificates)],
            "coverage_limitations": sorted({applicability[k][1]
                                            for k in unknown_missing}),
            "observed_positive_evidence_refs": [
                x["id"] for x in actual if x.get("id")],
            "final_canonical_revision": revision,
        }

    def _default_lvs(self, change: DesignChange) -> dict[str, Any]:
        ref = change.design_revision.flow_model_ref
        if ref is None:
            return {"overall_status": "UNKNOWN", "reason": "no_flow_model_ref"}
        from ..flow.service import FlowService
        flow_state = FlowService(self.store).get_flow(ref.identity)
        current_ref = (self._flow_design_digest(ref.identity)
                       if ref.revision.startswith("flow-design-v1:")
                       else _digest(flow_state))
        if current_ref != ref.revision:
            return {"overall_status": "UNKNOWN",
                    "reason": "flow_revision_identity_mismatch",
                    "design_revision_ref": vars(ref)}
        actual = (change.implementation or {}).get("current_evidence_revision")
        if actual != self._active_revision(change.repo_id) or (
            actual != self.store.current_snapshot(change.repo_id)
        ):
            return {"overall_status": "UNKNOWN",
                    "reason": "actual_revision_not_current",
                    "design_revision_ref": vars(ref)}
        from ..lvs.flow_runner import lvs_flow
        result = lvs_flow(self.store, ref.identity)
        result["design_revision_ref"] = vars(ref)
        result["rule_version"] = "software-lvs/1"
        return result

    def _default_reindex(self, change: DesignChange):
        repo = self.store.repo(change.repo_id)
        if not repo:
            raise DesignLifecycleError(
                "repo_not_found", "change repository is not registered")
        from ..indexer import Indexer
        return Indexer(
            self.store, repo["root_path"], repo_id=change.repo_id).index()

    def _index_job(self, job_id: str, repo_id: str) -> dict[str, Any]:
        job = self.job_lookup(job_id) if self.job_lookup else None
        if not job or job.get("repo_id") != repo_id or job.get("kind") != "index":
            raise DesignLifecycleError(
                "index_job_identity_mismatch", "index job is unavailable or mismatched")
        return job

    @staticmethod
    def _require_no_active_job(change: DesignChange) -> None:
        linked = (change.implementation or {}).get("index_job") or {}
        if linked.get("job_id") and not linked.get("adopted_at"):
            raise DesignLifecycleError(
                "index_job_active", "complete or fail the linked index job first")

    def _require_actual_current(self, change: DesignChange) -> None:
        actual = (change.implementation or {}).get("current_evidence_revision")
        active = self._active_revision(change.repo_id)
        if not actual or actual != active:
            raise DesignLifecycleError(
                "actual_revision_stale", "actual observation is no longer current",
                {"actual_revision": actual,
                 "current_revision": active})

    def _active_revision(self, repo_id: str) -> str | None:
        """Prefer the unique published lineage tip over millisecond ordering.

        A timestamp tie or diverged branch must not silently grant freshness.
        """
        rows = [x for x in self.store.snapshots(repo_id)
                if x.get("publication_status", "published") == "published"]
        parents = {x.get("parent_id") for x in rows if x.get("parent_id")}
        tips = [x for x in rows if x.get("id") not in parents]
        if len(tips) == 1:
            return tips[0]["id"]
        if not tips:
            return None
        newest = max(x.get("created_at", 0) for x in tips)
        latest = [x for x in tips if x.get("created_at", 0) == newest]
        return latest[0]["id"] if len(latest) == 1 else None

    def _implementation_from_index_result(
            self, change: DesignChange, result: dict[str, Any],
            selectors: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        revision = result["snapshot_id"]
        evidence = self._canonicalize_actual_evidence(
            change.repo_id, revision, selectors)
        implementation = dict(change.implementation or {})
        implementation.update({
            "actual_changed_files": list(result.get("changed_files", [])),
            "actual_changed_symbols": sorted(({
                str(x.get("source") or x.get("symbol") or "")
                for x in evidence
            } | {str(x.get("target") or "") for x in evidence}) - {""}),
            "current_evidence_revision": revision,
            "actual_evidence": evidence,
            "observation_status": "recorded",
            "incremental_run": {key: result.get(key) for key in (
                "run_id", "incremental", "files_parsed", "changed_files",
                "invalidated_files", "cutoff_reason",
                "canonical_reconsidered_facts", "projections_recomputed",
                "projections_reused", "duration_ms")},
        })
        return implementation

    def _canonicalize_actual_evidence(
            self, repo_id: str, snapshot_id: str,
            claims: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        """Resolve caller selectors against canonical rows; never trust a claim.

        Agent/human payloads select evidence only. Authority/truth/coverage,
        resolution and source location are reconstructed from the published
        canonical revision so an implementation claim cannot become proof.
        """
        edges = self.store.all_edges(repo_id, snapshot_id)
        out: list[dict[str, Any]] = []
        for claim in claims:
            kind = str(claim.get("kind") or "")
            source = str(claim.get("source") or "")
            target = str(claim.get("target") or "")
            row = next((e for e in edges
                        if e.get("kind") == kind
                        and e.get("src_id") == source
                        and e.get("dst_id") == target), None)
            if row is None:
                raise DesignLifecycleError(
                    "actual_evidence_not_canonical",
                    "implementation claim is absent from canonical revision",
                    {"revision": snapshot_id, "selector": {
                        "kind": kind, "source": source, "target": target}})
            support = [x for x in self.store.evidence_for(
                repo_id, snapshot_id, row["id"])
                if x.get("entity_type") == "edge"]
            spans = []
            for evidence_row in support:
                location = evidence_row.get("location_json")
                try:
                    parsed = json.loads(location) if isinstance(location, str) else location
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    spans.append(parsed)
            out.append({
                "id": row.get("id"), "kind": kind,
                "source": source, "target": target,
                "authority": "CANONICAL_CANDIDATE",
                "truth_class": "UNKNOWN", "coverage": "UNKNOWN",
                "resolution": "UNKNOWN", "execution_modality": "UNKNOWN",
                "provider": [],
                "source_span": spans[0] if len(spans) == 1 else None,
                "supporting_evidence_refs": [
                    f"{snapshot_id}:edge:{row['id']}:{i}"
                    for i in range(len(support))],
                "canonical_revision": snapshot_id,
            })
        return out

    def _assert_closable(self, change: DesignChange) -> None:
        checks = {
            "design_drc": bool(change.design_drc
                               and change.design_drc.get("acceptable")),
            "expected_actual": bool(change.expected_actual
                                    and change.expected_actual.get("outcome") == "MATCHED"),
            "lvs": bool(change.lvs_result
                        and change.lvs_result.get("overall_status") == "MATCH"),
            "verification": bool(change.verification),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise DesignLifecycleError(
                "close_gate_failed", "change is not eligible for close",
                {"failed": failed})
        if change.scope.get("approval_required"):
            approvals = [r for r in self.repo.independent_receipts("approval", change.id)
                         if r["design_revision"] == change.design_revision.id]
            latest = approvals[-1] if approvals else None
            if (not latest or latest["decision"] != "APPROVE" or
                    latest["change_version"] != change.version or
                    not latest.get("principal_id") or
                    not latest.get("auth_session_id") or
                    not latest.get("rintel_instance_id")):
                raise DesignLifecycleError(
                    "approval_required", "authenticated approval receipt required")
