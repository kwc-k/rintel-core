"""Git-native collaboration orchestration for bounded Agent workspaces.

Git owns commits, branches, worktrees, merge-base and textual merges.  This
module owns assignments, immutable base identities, governance checks and a
single merge eligibility projection.  A worktree is not a security sandbox.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .execution_authority import ExecutionAuthority, ExecutionError, ExecutionRequest
from .git_integration_authority import GitIntegrationAuthority
from .local_owner_auth import LocalOwnerPrincipal


class GitWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()
    return slug[:40] or "task"


class GitWorkspaceManager:
    """Deep module: Git plumbing + Agent governance behind one interface."""

    def __init__(self, state_root: str | Path, *,
                 execution_authority: ExecutionAuthority | None = None,
                 integration_authority: GitIntegrationAuthority | None = None,
                 clock: Callable[[], float] | None = None):
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.worktree_root = self.state_root / "worktrees"
        self.worktree_root.mkdir(exist_ok=True)
        self.execution_authority = execution_authority
        self.integration_authority = integration_authority
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._workspaces: dict[str, dict] = {}
        self._integrations: dict[str, dict] = {}
        self._load()

    def _run(self, repository: str | Path, *args: str,
             check: bool = True) -> subprocess.CompletedProcess[str]:
        root = Path(repository).resolve()
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
            text=True, capture_output=True, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if check and result.returncode:
            raise GitWorkspaceError(
                "GIT_COMMAND_FAILED", "Git command failed",
                {"args": list(args), "returncode": result.returncode,
                 "stderr": result.stderr.strip()})
        return result

    def _load(self) -> None:
        path = self.state_root / "state.json"
        if not path.exists():
            return
        state = json.loads(path.read_text())
        self._workspaces = {row["workspace_id"]: row
                            for row in state.get("workspaces", [])}
        self._integrations = {row["integration_id"]: row
                              for row in state.get("integrations", [])}

    def _save(self) -> None:
        value = {"schema_version": "git-workspace/1",
                 "workspaces": list(self._workspaces.values()),
                 "integrations": list(self._integrations.values())}
        temporary = self.state_root / f"state.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2))
        temporary.replace(self.state_root / "state.json")

    def inspect_repository(self, repository_root: str | Path,
                           integration_branch: str | None = None) -> dict:
        candidate = Path(repository_root).resolve()
        result = self._run(candidate, "rev-parse", "--show-toplevel", check=False)
        if result.returncode:
            raise GitWorkspaceError("GIT_UNAVAILABLE", "path is not a Git repository",
                                    {"path": str(candidate)})
        root = Path(result.stdout.strip()).resolve()
        git_dir = Path(self._run(root, "rev-parse", "--absolute-git-dir").stdout.strip())
        common = Path(self._run(root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
        head_result = self._run(root, "rev-parse", "HEAD", check=False)
        head = head_result.stdout.strip() if head_result.returncode == 0 else None
        branch_result = self._run(root, "symbolic-ref", "--short", "HEAD", check=False)
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        branch = integration_branch or self._default_branch(root, current_branch)
        roots = []
        if head:
            roots = sorted(self._run(root, "rev-list", "--max-parents=0", "HEAD")
                           .stdout.splitlines())
        porcelain = self._run(root, "status", "--porcelain=v1").stdout.splitlines()
        state = ("DETACHED_HEAD" if current_branch is None and head else
                 "DIRTY_WORKTREE" if porcelain else "CLEAN")
        return {
            "repository_root": str(root), "git_dir": str(git_dir.resolve()),
            "git_common_dir": str(common.resolve()),
            "repository_identity": f"git-history:{_digest(roots)}",
            "root_commits": roots, "integration_branch": branch,
            "current_branch": current_branch, "head_commit": head,
            "working_tree_state": state,
        }

    def _default_branch(self, root: Path, current: str | None) -> str:
        symbolic = self._run(root, "symbolic-ref", "--short",
                             "refs/remotes/origin/HEAD", check=False)
        if symbolic.returncode == 0:
            return symbolic.stdout.strip().removeprefix("origin/")
        for branch in ("main", "master"):
            if self._run(root, "show-ref", "--verify", "--quiet",
                         f"refs/heads/{branch}", check=False).returncode == 0:
                return branch
        if current:
            return current
        raise GitWorkspaceError("MISSING_BRANCH", "no integration branch is available")

    def create_agent_worktree(
        self, *, repository_root: str | Path, agent_id: str, task_id: str,
        change_id: str, base_commit: str, base_change_version: int,
        base_design_revision: str, base_canonical_revision: str,
        allowed_scope: list[str], allowed_operations: list[str],
    ) -> dict:
        with self._lock:
            repo = self.inspect_repository(repository_root)
            root = Path(repo["repository_root"])
            if repo["working_tree_state"] == "DETACHED_HEAD":
                raise GitWorkspaceError("DETACHED_HEAD", "source repository is detached")
            verified = self._run(root, "rev-parse", f"{base_commit}^{{commit}}", check=False)
            if verified.returncode or verified.stdout.strip() != base_commit:
                raise GitWorkspaceError("INVALID_BASE_COMMIT", "base commit is unavailable")
            scopes = self._validate_scope(allowed_scope)
            active = [row for row in self._workspaces.values()
                      if row["agent_principal_id"] == agent_id
                      and row["task_id"] == task_id and row["status"] != "RETIRED"]
            if active:
                raise GitWorkspaceError("ACTIVE_WORKTREE_EXISTS",
                                        "agent task already has a writable worktree")
            workspace_id = f"agent-workspace-{uuid.uuid4().hex}"
            branch = f"rintel/agent/{_slug(agent_id)}-{_slug(task_id)}-{workspace_id[-8:]}"
            path = self.worktree_root / workspace_id
            self._run(root, "worktree", "add", "--lock", "-b", branch,
                      str(path), base_commit)
            issued = int(self.clock())
            agent_session_token = secrets.token_urlsafe(32)
            principal = {
                "agent_principal_id": f"rintel-agent-{uuid.uuid4().hex}",
                "agent_id": agent_id, "task_id": task_id, "change_id": change_id,
                "workspace_id": workspace_id, "issuer": "Rintel",
                "agent_session_identity": f"agent-session-{uuid.uuid4().hex}",
                "issued_at": issued, "expires_at": None,
                "base_commit": base_commit,
                "base_change_version": base_change_version,
                "base_design_revision": base_design_revision,
                "base_canonical_revision": base_canonical_revision,
                "allowed_scope": scopes,
                "allowed_operations": sorted(set(allowed_operations)),
                "human_authority": "FORBIDDEN",
            }
            row = {
                "workspace_id": workspace_id,
                "agent_principal_id": principal["agent_principal_id"],
                "agent_principal": principal, "agent_id": agent_id,
                "agent_session_digest": hashlib.sha256(
                    agent_session_token.encode()).hexdigest(),
                "task_id": task_id, "change_id": change_id,
                "repository_identity": repo["repository_identity"],
                "repository_root": str(root), "branch_ref": branch,
                "worktree_path": str(path), "base_commit": base_commit,
                "base_change_version": base_change_version,
                "base_design_revision": base_design_revision,
                "base_canonical_revision": base_canonical_revision,
                "current_head": base_commit, "allowed_scope": scopes,
                "allowed_operations": principal["allowed_operations"],
                "created_at": issued, "status": "ACTIVE",
                "submitted_result": None,
            }
            self._workspaces[workspace_id] = row
            self._save()
            result = json.loads(json.dumps(row))
            result.pop("agent_session_digest", None)
            result["agent_session_token"] = agent_session_token
            return result

    def _validate_scope(self, scopes: list[str]) -> list[str]:
        if not scopes:
            raise GitWorkspaceError("SCOPE_REQUIRED", "at least one allowed path is required")
        out = []
        for raw in scopes:
            value = str(PurePosixPath(raw))
            if value.startswith("/") or value == ".." or value.startswith("../"):
                raise GitWorkspaceError("INVALID_SCOPE", "scope must be repository relative")
            out.append(value)
        return sorted(set(out))

    def _workspace(self, workspace_id: str) -> dict:
        row = self._workspaces.get(workspace_id)
        if not row:
            raise GitWorkspaceError("MISSING_WORKTREE", "agent workspace is unavailable")
        return row

    def assigned_workspace(self, workspace_id: str, session_token: str) -> dict:
        row = self._workspace(workspace_id)
        supplied = hashlib.sha256(str(session_token).encode()).hexdigest()
        if not secrets.compare_digest(supplied, row["agent_session_digest"]):
            raise GitWorkspaceError("AGENT_SESSION_REQUIRED",
                                    "valid assigned Agent session required")
        result = self.inspect_worktree(workspace_id)
        result.pop("agent_session_digest", None)
        return result

    def submit_agent_result(self, workspace_id: str, session_token: str) -> dict:
        self.assigned_workspace(workspace_id, session_token)
        return self.submit_committed_result(workspace_id)

    def request_workspace_execution(
            self, workspace_id: str, session_token: str, *, profile_id: str,
            kind: str, parameters: tuple[tuple[str, str], ...] = ()) -> dict:
        """Run one host-owned profile at the Agent's exact clean Git head."""
        with self._lock:
            observed = self.assigned_workspace(workspace_id, session_token)
            if kind not in {"BUILD", "TEST"}:
                raise GitWorkspaceError("INVALID_OPERATION", "only BUILD or TEST is supported")
            if kind not in observed.get("allowed_operations", []):
                raise GitWorkspaceError(
                    "OPERATION_NOT_ALLOWED", f"{kind} is not authorized for this workspace")
            if observed.get("status") in {"DIRTY_WORKTREE", "DETACHED_HEAD",
                                           "MISSING_WORKTREE", "RETIRED"}:
                raise GitWorkspaceError(observed["status"],
                                        "workspace is not executable")
            if self.execution_authority is None:
                raise GitWorkspaceError("EXECUTION_AUTHORITY_UNAVAILABLE",
                                        "trusted execution authority is unavailable")
            row = self._workspace(workspace_id)
            try:
                receipt = self.execution_authority.execute_in_git_worktree(
                    ExecutionRequest(
                        profile_id=profile_id, kind=kind, parameters=parameters,
                        change_id=row["change_id"],
                        canonical_revision=row["base_canonical_revision"],
                        design_revision=row["base_design_revision"],
                        expected_source_revision=None,
                        lineage_ref=f"agent-workspace:{workspace_id}"),
                    workspace_root=row["worktree_path"],
                    expected_git_commit=observed["current_head"],
                    workspace_id=workspace_id,
                    principal_id=row["agent_principal_id"])
            except ExecutionError as exc:
                raise GitWorkspaceError("EXECUTION_DENIED", str(exc)) from exc
            row.setdefault("execution_receipts", []).append({
                "execution_id": receipt["execution_id"], "kind": kind,
                "source_git_commit": receipt["source_git_commit"],
                "result": receipt["result"]})
            self._save()
            return receipt

    def list_for_change(self, change_id: str) -> dict:
        workspaces = []
        for row in self._workspaces.values():
            if row["change_id"] != change_id:
                continue
            public = self.inspect_worktree(row["workspace_id"])
            public.pop("agent_session_digest", None)
            public.pop("agent_session_token", None)
            workspaces.append(public)
        workspace_ids = {row["workspace_id"] for row in workspaces}
        integrations = [json.loads(json.dumps(row))
                        for row in self._integrations.values()
                        if workspace_ids.intersection(row["workspace_ids"])]
        return {"schema_version": "git-collaboration/1",
                "workspaces": workspaces, "integrations": integrations}

    def inspect_worktree(self, workspace_id: str) -> dict:
        with self._lock:
            row = self._workspace(workspace_id)
            path = Path(row["worktree_path"])
            if row["status"] == "RETIRED":
                return json.loads(json.dumps(row))
            if not path.is_dir():
                row["status"] = "MISSING_WORKTREE"
                self._save()
                return json.loads(json.dumps(row))
            head = self._run(path, "rev-parse", "HEAD").stdout.strip()
            branch = self._run(path, "symbolic-ref", "--short", "HEAD", check=False)
            dirty = bool(self._run(path, "status", "--porcelain=v1").stdout.strip())
            target = self.inspect_repository(row["repository_root"])["integration_branch"]
            target_head = self._run(row["repository_root"], "rev-parse", target).stdout.strip()
            row["current_head"] = head
            row["status"] = ("DETACHED_HEAD" if branch.returncode else
                             "DIRTY_WORKTREE" if dirty else
                             "BRANCH_STALE" if target_head != row["base_commit"] else
                             "SUBMITTED" if row.get("submitted_result") else "ACTIVE")
            row["target_head"] = target_head
            self._save()
            return json.loads(json.dumps(row))

    def create_branch_evidence(self, workspace_id: str) -> dict:
        row = self.inspect_worktree(workspace_id)
        return {
            "snapshot_id": f"branch-evidence-{uuid.uuid4().hex}",
            "workspace_id": workspace_id, "source_commit": row["current_head"],
            "base_commit": row["base_commit"],
            "base_design_revision": row["base_design_revision"],
            "base_canonical_revision": row["base_canonical_revision"],
            "publication_policy": "CANDIDATE_ONLY",
            "canonical_current_mutation": "FORBIDDEN",
            "created_at": int(self.clock()),
        }

    def submit_committed_result(self, workspace_id: str) -> dict:
        with self._lock:
            observed = self.inspect_worktree(workspace_id)
            if observed["status"] == "DIRTY_WORKTREE":
                raise GitWorkspaceError("DIRTY_WORKTREE", "commit result before submission")
            if observed["status"] == "DETACHED_HEAD":
                raise GitWorkspaceError("DETACHED_HEAD", "workspace branch is detached")
            row = self._workspace(workspace_id)
            head = row["current_head"]
            if head == row["base_commit"]:
                raise GitWorkspaceError("UNCOMMITTED", "workspace has no committed result")
            ancestry = self._run(row["worktree_path"], "merge-base", "--is-ancestor",
                                 row["base_commit"], head, check=False)
            if ancestry.returncode:
                raise GitWorkspaceError("BASE_IDENTITY_MISMATCH",
                                        "result does not descend from frozen base")
            changed = sorted(self._run(row["worktree_path"], "diff", "--name-only",
                                       f"{row['base_commit']}..{head}").stdout.splitlines())
            violations = [path for path in changed
                          if not any(self._scope_matches(path, rule)
                                     for rule in row["allowed_scope"])]
            fields = self._run(row["worktree_path"], "show", "-s",
                               "--format=%H%x00%P%x00%T%x00%an%x00%ae%x00%aI", head
                               ).stdout.strip().split("\0")
            result = {
                "workspace_id": workspace_id, "commit_sha": fields[0],
                "parents": fields[1].split(), "base_commit": row["base_commit"],
                "tree_hash": fields[2], "author_name": fields[3],
                "author_email": fields[4], "timestamp": fields[5],
                "agent_principal_id": row["agent_principal_id"],
                "task_id": row["task_id"], "change_id": row["change_id"],
                "changed_files": changed, "scope_violations": violations,
                "scope_status": "SCOPE_VIOLATION" if violations else "IN_SCOPE",
                "merge_eligible": not violations,
                "base_identities": {key: row[key] for key in (
                    "base_commit", "base_change_version", "base_design_revision",
                    "base_canonical_revision")},
            }
            row["submitted_result"] = result
            row["status"] = "SCOPE_VIOLATION" if violations else "SUBMITTED"
            self._save()
            return json.loads(json.dumps(result))

    @staticmethod
    def _scope_matches(path: str, rule: str) -> bool:
        if fnmatch.fnmatchcase(path, rule):
            return True
        if rule.endswith("/**"):
            return path.startswith(rule[:-3].rstrip("/") + "/")
        return path == rule

    def retire_worktree(self, workspace_id: str) -> dict:
        with self._lock:
            row = self.inspect_worktree(workspace_id)
            if row["status"] == "RETIRED":
                return row
            if row["status"] == "DIRTY_WORKTREE":
                raise GitWorkspaceError("DIRTY_WORKTREE", "dirty worktree cannot be retired")
            path = Path(row["worktree_path"])
            actual = self._run(path, "rev-parse", "HEAD").stdout.strip()
            if actual != row["current_head"]:
                raise GitWorkspaceError("WORKTREE_HEAD_MISMATCH", "worktree head changed")
            self._run(row["repository_root"], "worktree", "unlock", str(path))
            self._run(row["repository_root"], "worktree", "remove", str(path))
            self._run(row["repository_root"], "worktree", "prune")
            row["status"] = "RETIRED"
            row["retired_at"] = int(self.clock())
            self._save()
            return json.loads(json.dumps(row))

    def create_integration(self, *, repository_root: str | Path,
                           workspace_ids: list[str], target_branch: str) -> dict:
        with self._lock:
            repo = self.inspect_repository(repository_root, target_branch)
            root = Path(repo["repository_root"])
            target_head_result = self._run(root, "rev-parse",
                                           f"refs/heads/{target_branch}", check=False)
            if target_head_result.returncode:
                raise GitWorkspaceError("MISSING_BRANCH", "target branch is unavailable")
            if not workspace_ids:
                raise GitWorkspaceError("CANDIDATE_REQUIRED", "candidate workspaces required")
            rows = [self._workspace(item) for item in workspace_ids]
            if any(row["repository_identity"] != repo["repository_identity"] for row in rows):
                raise GitWorkspaceError("REPOSITORY_MISMATCH", "candidate repositories differ")
            if any(not row.get("submitted_result") for row in rows):
                raise GitWorkspaceError("UNCOMMITTED", "all workspaces need submitted commits")
            if any(row["submitted_result"]["scope_status"] != "IN_SCOPE" for row in rows):
                raise GitWorkspaceError("SCOPE_VIOLATION", "out-of-scope result cannot integrate")
            target_head = target_head_result.stdout.strip()
            integration_id = f"integration-{uuid.uuid4().hex}"
            branch = f"rintel/integration/{integration_id[-12:]}"
            path = self.worktree_root / integration_id
            self._run(root, "worktree", "add", "--lock", "-b", branch,
                      str(path), target_head)
            commits = [row["submitted_result"]["commit_sha"] for row in rows]
            conflict_files: list[str] = []
            status = "CLEAN_MERGE"
            for commit in commits:
                merge = self._run(path, "merge", "--no-ff", "--no-edit", commit,
                                  check=False)
                if merge.returncode:
                    conflict_files = sorted(self._run(
                        path, "diff", "--name-only", "--diff-filter=U").stdout.splitlines())
                    status = "TEXT_CONFLICT" if conflict_files else "MERGE_ABORTED"
                    break
            head = self._run(path, "rev-parse", "HEAD").stdout.strip()
            merge_bases = sorted({self._run(root, "merge-base", target_head, commit)
                                  .stdout.strip() for commit in commits})
            candidate_changed = sorted({name for item in rows for name in
                                        item["submitted_result"]["changed_files"]})
            stale = any(row["base_commit"] != target_head for row in rows)
            row = {
                "integration_id": integration_id,
                "change_id": rows[0]["change_id"],
                "repository_identity": repo["repository_identity"],
                "repository_root": str(root), "target_branch": target_branch,
                "target_head": target_head, "merge_bases": merge_bases,
                "candidate_commits": commits, "workspace_ids": list(workspace_ids),
                "integration_branch": branch,
                "integration_worktree": str(path), "integration_head": head,
                "integration_tree": self._run(path, "rev-parse", "HEAD^{tree}").stdout.strip(),
                "git_status": status, "conflicted_files": conflict_files,
                "branch_status": "BRANCH_STALE" if stale else "CURRENT_BASE",
                "candidate_changed_files": candidate_changed,
                "agent_scope_status": ("IN_SCOPE" if all(
                    item["submitted_result"]["scope_status"] == "IN_SCOPE"
                    for item in rows) else "SCOPE_VIOLATION"),
                "created_at": int(self.clock()), "canonical_publication": "NOT_PUBLISHED",
            }
            self._integrations[integration_id] = row
            self._save()
            return json.loads(json.dumps(row))

    def audit_integration(
            self, integration_id: str, *, build_profile_id: str,
            test_profile_id: str,
            build_parameters: tuple[tuple[str, str], ...] = (),
            test_parameters: tuple[tuple[str, str], ...] = (),
            authority: GitIntegrationAuthority | None = None) -> dict:
        """Run candidate indexing and every merge authority at one exact SHA."""
        with self._lock:
            row = self._integration(integration_id)
            trusted_authority = authority or self.integration_authority
            if trusted_authority is None:
                raise GitWorkspaceError("INTEGRATION_AUTHORITY_UNAVAILABLE",
                                        "trusted integration authority is unavailable")
            path = Path(row["integration_worktree"])
            if self._run(path, "status", "--porcelain=v1").stdout.strip():
                raise GitWorkspaceError("DIRTY_WORKTREE",
                                        "integration worktree must be clean")
            head = self._run(path, "rev-parse", "HEAD").stdout.strip()
            tree = self._run(path, "rev-parse", "HEAD^{tree}").stdout.strip()
            if head != row["integration_head"] or tree != row["integration_tree"]:
                self._invalidate_integration(row, "STALE_INTEGRATION_HEAD")
                raise GitWorkspaceError("STALE_INTEGRATION_HEAD",
                                        "integration head changed; rebuild candidate")
            target = self._run(row["repository_root"], "rev-parse",
                               row["target_branch"]).stdout.strip()
            if target != row["target_head"]:
                self._invalidate_integration(row, "STALE_TARGET_HEAD")
                raise GitWorkspaceError("STALE_TARGET_HEAD",
                                        "target advanced; rebuild candidate")
            authority_input = json.loads(json.dumps(row))
            # Candidate revision is allocated by the authority; this placeholder
            # is replaced in the receipt-bound execution request during audit.
            try:
                projection = trusted_authority.audit(
                    authority_input, build_profile_id=build_profile_id,
                    test_profile_id=test_profile_id,
                    build_parameters=build_parameters,
                    test_parameters=test_parameters)
            except Exception as exc:
                raise GitWorkspaceError("INTEGRATION_AUDIT_FAILED", str(exc)) from exc
            row["latest_projection"] = projection
            row["candidate_canonical_revision"] = projection[
                "candidate_index"]["candidate_canonical_revision"]
            self._save()
            return json.loads(json.dumps(projection))

    @staticmethod
    def _invalidate_integration(row: dict, reason: str) -> None:
        projection = row.get("latest_projection")
        if projection:
            projection["evidence_freshness"] = "STALE"
            projection["merge_eligibility"] = "NOT_MERGE_ELIGIBLE"
            projection["diagnostics"] = sorted(set(
                projection.get("diagnostics", []) + [reason]))

    def _integration(self, integration_id: str) -> dict:
        row = self._integrations.get(integration_id)
        if not row:
            raise GitWorkspaceError("MISSING_INTEGRATION", "integration is unavailable")
        return row

    def get_integration(self, integration_id: str) -> dict:
        return json.loads(json.dumps(self._integration(integration_id)))

    def project_merge(self, integration_id: str, *, checks: dict[str, str]) -> dict:
        """Deprecated compatibility surface; caller claims never grant authority."""
        with self._lock:
            row = self._integration(integration_id)
            projection = {
                "schema_version": "merge-candidate/2",
                **{key: row[key] for key in (
                    "integration_id", "repository_identity", "target_branch",
                    "target_head", "merge_bases", "candidate_commits",
                    "workspace_ids", "integration_branch", "integration_worktree",
                    "integration_head", "integration_tree", "git_status",
                    "conflicted_files", "branch_status", "canonical_publication")},
                "checks": {key: "UNTRUSTED_CALLER_CLAIM" for key in checks},
                "diagnostics": ["TRUSTED_INTEGRATION_AUDIT_REQUIRED"],
                "evidence_freshness": "UNAVAILABLE",
                "merge_eligibility": "NOT_MERGE_ELIGIBLE",
            }
            row["latest_projection"] = projection
            self._save()
            return json.loads(json.dumps(projection))
    def merge_authorized(self, integration_id: str,
                         principal: LocalOwnerPrincipal) -> dict:
        """Fast-forward the target only after owner auth + current eligibility."""
        with self._lock:
            row = self._integration(integration_id)
            projection = row.get("latest_projection")
            if not projection or projection.get("merge_eligibility") != "MERGE_ELIGIBLE":
                raise GitWorkspaceError("MERGE_NOT_ELIGIBLE",
                                        "integration has not passed current checks")
            if (principal.principal_type != "LOCAL_OWNER"
                    or principal.authorization != "OWNER"
                    or principal.expires_at <= int(self.clock())):
                raise GitWorkspaceError("OWNER_AUTHORIZATION_REQUIRED",
                                        "authenticated Local Owner required")
            root = Path(row["repository_root"])
            repo = self.inspect_repository(root, row["target_branch"])
            if (repo["current_branch"] != row["target_branch"]
                    or repo["working_tree_state"] != "CLEAN"):
                raise GitWorkspaceError("HUMAN_WORKTREE_UNAVAILABLE",
                                        "target worktree must be clean on target branch")
            if repo["head_commit"] != row["target_head"]:
                self._invalidate_integration(row, "STALE_TARGET_HEAD")
                self._save()
                raise GitWorkspaceError("STALE_TARGET_HEAD", "target branch advanced")
            integration_head = self._run(
                row["integration_worktree"], "rev-parse", "HEAD").stdout.strip()
            integration_tree = self._run(
                row["integration_worktree"], "rev-parse", "HEAD^{tree}").stdout.strip()
            if (integration_head != row["integration_head"] or
                    integration_tree != row["integration_tree"] or
                    projection.get("integration_head") != integration_head or
                    projection.get("target_head") != repo["head_commit"]):
                self._invalidate_integration(row, "STALE_INTEGRATION_HEAD")
                self._save()
                raise GitWorkspaceError("STALE_INTEGRATION_HEAD",
                                        "integration head changed after audit")
            self._run(root, "merge", "--ff-only", row["integration_branch"])
            merged = self._run(root, "rev-parse", "HEAD").stdout.strip()
            row["merged_commit"] = merged
            row["merged_at"] = int(self.clock())
            row["merged_by_principal"] = principal.principal_id
            row["git_merge_status"] = "MERGED"
            row["canonical_publication"] = "PENDING_INDEXER_OBSERVATION"
            self._save()
            return {
                "integration_id": integration_id, "status": "MERGED",
                "source_revision": merged, "target_branch": row["target_branch"],
                "principal_id": principal.principal_id,
                "canonical_publication": "PENDING_INDEXER_OBSERVATION",
            }

    def observe_merged(self, integration_id: str, *,
                       authority: GitIntegrationAuthority | None = None) -> dict:
        """Advance Canonical CURRENT only through production Indexer output."""
        with self._lock:
            row = self._integration(integration_id)
            if row.get("canonical_publication") != "PENDING_INDEXER_OBSERVATION":
                raise GitWorkspaceError("OBSERVATION_NOT_PENDING",
                                        "integration is not awaiting Indexer observation")
            trusted_authority = authority or self.integration_authority
            if trusted_authority is None:
                raise GitWorkspaceError("INTEGRATION_AUTHORITY_UNAVAILABLE",
                                        "trusted integration authority is unavailable")
            current_head = self._run(row["repository_root"], "rev-parse", "HEAD").stdout.strip()
            if current_head != row.get("merged_commit"):
                raise GitWorkspaceError("MERGED_HEAD_CHANGED",
                                        "target head no longer equals merged commit")
            receipt = trusted_authority.observe_merged_commit(
                json.loads(json.dumps(row)))
            row["production_observation"] = receipt
            row["canonical_publication"] = receipt["status"]
            self._save()
            return json.loads(json.dumps(receipt))

    def retire_integration(self, integration_id: str, *,
                           abort_conflict: bool = False) -> dict:
        """Remove only a verified clean managed worktree; retain its branch."""
        with self._lock:
            row = self._integration(integration_id)
            if row.get("status") == "RETIRED":
                return json.loads(json.dumps(row))
            path = Path(row["integration_worktree"])
            if not path.is_dir():
                raise GitWorkspaceError("MISSING_WORKTREE", "integration worktree missing")
            dirty = bool(self._run(path, "status", "--porcelain=v1").stdout.strip())
            if dirty and not abort_conflict:
                raise GitWorkspaceError("DIRTY_WORKTREE",
                                        "dirty integration cannot be retired silently")
            if dirty:
                aborted = self._run(path, "merge", "--abort", check=False)
                if aborted.returncode or self._run(
                        path, "status", "--porcelain=v1").stdout.strip():
                    raise GitWorkspaceError("MERGE_ABORTED",
                                            "integration conflict could not be cleaned")
            actual = self._run(path, "rev-parse", "HEAD").stdout.strip()
            expected = row["integration_head"]
            if actual != expected:
                raise GitWorkspaceError("WORKTREE_HEAD_MISMATCH",
                                        "integration head changed since inspection")
            self._run(row["repository_root"], "worktree", "unlock", str(path))
            self._run(row["repository_root"], "worktree", "remove", str(path))
            self._run(row["repository_root"], "worktree", "prune")
            row["status"] = "RETIRED"
            row["retired_at"] = int(self.clock())
            row["branch_retained"] = row["integration_branch"]
            self._save()
            return json.loads(json.dumps(row))
