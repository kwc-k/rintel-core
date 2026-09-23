import { apiFetch, apiGet, apiPost, qs } from './client'
import type {
  AcceptanceCriterion, DesignChange, DesignCommand, DesignRef,
} from '../domain/design-lifecycle'
import { validateChangeWorkspace } from '../domain/change-workspace'
import type { ChangeWorkspaceProjection } from '../domain/change-workspace'

export interface OpenDesignChange {
  repo_id: string
  base_canonical_revision: string
  intent: string
  scope: Record<string, unknown>
  acceptance_criteria: AcceptanceCriterion[]
  actor: string
  architecture_proposal_ref?: DesignRef
  flow_model_ref?: DesignRef
}

export interface DesignMutationResponse<T> extends DesignChange {
  mutation_result: T
}

let activeDesignChangeId: string | null = null

export function setActiveDesignChange(changeId: string | null): void {
  activeDesignChangeId = changeId
}

function requireActiveDesignChange(): string {
  if (!activeDesignChangeId) {
    throw new Error('Design mutation requires an active DesignChange (change_id)')
  }
  return activeDesignChangeId
}

export function openDesignChange(body: OpenDesignChange): Promise<DesignChange> {
  return apiPost<DesignChange>('/design-changes', body).then((change) => {
    setActiveDesignChange(change.id)
    return change
  })
}

export function applyDesignCommand(
  changeId: string, command: DesignCommand,
): Promise<DesignChange> {
  return apiPost<DesignChange>(`/design-changes/${changeId}/commands`, command)
    .then((change) => {
      setActiveDesignChange(change.id)
      return change
    })
}

export function applyDesignMutation<T>(
  plane: 'architecture' | 'flow', operation: string,
  payload: Record<string, unknown>, actor = 'human:ui',
): Promise<T> {
  const changeId = requireActiveDesignChange()
  return apiPost<DesignMutationResponse<T>>(
    `/design-changes/${changeId}/commands`,
    { command: 'design_mutation', actor, plane, operation, payload },
  ).then((response) => response.mutation_result)
}

export function getDesignChange(changeId: string): Promise<DesignChange> {
  return apiGet<DesignChange>(`/design-changes/${changeId}`).then((change) => {
    setActiveDesignChange(change.id)
    return change
  })
}

export function listDesignChanges(repoId?: string): Promise<{ changes: DesignChange[] }> {
  return apiGet<{ changes: DesignChange[] }>(
    `/design-changes${qs({ repo_id: repoId })}`,
  )
}

export async function getChangeWorkspace(
  changeId: string,
): Promise<ChangeWorkspaceProjection> {
  return validateChangeWorkspace(await apiGet<unknown>(
    `/design-changes/${encodeURIComponent(changeId)}/workspace`))
}

export interface LocalOwnerSession {
  authenticated: boolean
  principal: null | {
    principal_id: string
    principal_type: 'LOCAL_OWNER'
    auth_session_id: string
    rintel_instance_id: string
    expires_at: number
    authorization: 'OWNER'
  }
  policy_version: string
  deployment?: 'LOCAL_SINGLE_OWNER'
}

export function getLocalOwnerSession(): Promise<LocalOwnerSession> {
  return apiGet('/local-owner/session')
}

export function bootstrapLocalOwner(token: string): Promise<LocalOwnerSession> {
  return apiPost('/local-owner/bootstrap', { token })
}

export function logoutLocalOwner(): Promise<{authenticated: false; revoked: boolean}> {
  return apiFetch('/local-owner/logout', {
    method: 'POST', headers: { 'X-Rintel-Owner-Intent': 'logout' },
  })
}

export function approveDesignChange(changeId: string, body: {
  decision: 'APPROVE' | 'REJECT'
  expected_version: number
  expected_design_revision: string
}): Promise<{id: string; receipt_digest: string}> {
  return apiFetch(`/design-changes/${encodeURIComponent(changeId)}/approvals`, {
    method: 'POST', headers: { 'Content-Type': 'application/json',
      'X-Rintel-Owner-Intent': 'approve' }, body: JSON.stringify(body),
  })
}

export interface GitCollaborationProjection {
  schema_version: 'git-collaboration/1'
  repository: Record<string, unknown>
  workspaces: Array<{
    workspace_id: string; agent_id: string; task_id: string; branch_ref: string
    base_commit: string; current_head: string; status: string
    allowed_scope: string[]; submitted_result?: null | {
      changed_files: string[]; scope_status: string; commit_sha: string
    }
  }>
  integrations: Array<{
    integration_id: string; target_head: string; integration_head: string
    git_status: string; branch_status: string; conflicted_files: string[]
    canonical_publication: string
    latest_projection?: { merge_eligibility: string; diagnostics: string[]
      checks?: Record<string, string>; evidence_freshness: string
      semantic?: { candidate_canonical_identity: string; unresolved_count: number }
      design?: { drc: { result: { acceptable: boolean } }
        lvs: { result: { overall_status: string } }
        alignment: { runtime_binding: string } }
      execution?: { build: { result: string; execution_id: string }
        test: { result: string; execution_id: string } }
    }
    production_observation?: { status: string; production_current_after?: string
      published_canonical_source_git_commit?: string }
  }>
}

export function getGitCollaboration(changeId: string): Promise<GitCollaborationProjection> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/git-collaboration`)
}

export function getHistoricalDesignRevision(
  changeId: string, revisionId: string,
): Promise<unknown> {
  return apiGet<unknown>(
    `/design-changes/${encodeURIComponent(changeId)}/design-revisions/${encodeURIComponent(revisionId)}`)
}

export interface BoundSourceRequest {
  revision: string
  entity_type: 'node' | 'edge'
  entity_id: string
  evidence_ref: string
  path: string
  start_line: number
  end_line: number
}

export function resolveBoundSource(changeId: string, request: BoundSourceRequest): Promise<{
  status: 'RESOLVED'; subject: string; canonical_revision: string
  entity_type: 'node' | 'edge'; entity_id: string; evidence_ref: string
  source_span: {path: string; start_line: number; end_line: number}
  content: string; etag: string
}> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/source${qs({ ...request })}`)
}

export function requestDesignReindex(changeId: string, actor: string): Promise<{
  change_id: string; change_version: number; job_id: string; status: 'pending'
}> {
  return apiPost(`/design-changes/${encodeURIComponent(changeId)}/reindex-jobs`, { actor })
}

export function adoptDesignIndexJob(changeId: string, jobId: string, actor: string): Promise<DesignChange> {
  return apiPost(`/design-changes/${encodeURIComponent(changeId)}/reindex-jobs/${encodeURIComponent(jobId)}/adopt`,
    { actor, actual_evidence_selectors: [] })
}

export interface ChangeWorkspaceSummary {
  id: string; repo_id: string; intent: string; state: string; version: number
  baseline_revision: string; design_revision: string; actual_revision: string | null
}

export async function listChangeWorkspaces(repoId?: string): Promise<ChangeWorkspaceSummary[]> {
  const raw = await apiGet<unknown>(`/design-changes/workspace-list${qs({ repo_id: repoId })}`)
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)
      || (raw as Record<string, unknown>).schema_version !== 'change-workspace-list/1'
      || !Array.isArray((raw as Record<string, unknown>).changes)) {
    throw new Error('Invalid Change Workspace list projection')
  }
  const changes = (raw as {changes: unknown[]}).changes
  for (const item of changes) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error('Invalid Change Workspace summary')
    }
    const row = item as Record<string, unknown>
    if (['id', 'repo_id', 'intent', 'state', 'baseline_revision', 'design_revision']
      .some((key) => typeof row[key] !== 'string')
      || typeof row.version !== 'number'
      || (row.actual_revision !== null && typeof row.actual_revision !== 'string')) {
      throw new Error('Invalid Change Workspace summary')
    }
  }
  return changes as ChangeWorkspaceSummary[]
}

export interface BuildAttemptView {
  attempt_id: string
  execution_id?: string
  profile_id: string
  started_ns: number
  exit_status: number | null
  verification_status: string
  previous_attempt: string | null
  application_id: string | null
  source_revision: string
  stdout_ref: string
  stderr_ref: string
  stdout_sha256: string
  stderr_sha256: string
  diagnostics: Array<{ id: string; severity: string; category: string; message: string;
    source_span: {path: string; start_line: number; end_line?: number} | null; authority: string }>
}

export interface BuildArtifactView {
  attempt_id: string
  artifact_id: string
  kind: 'STDOUT' | 'STDERR'
  content_type: string
  encoding: 'utf-8' | 'hex-escaped'
  size: number
  sha256: string
  truncated: boolean
  capture_truncated: boolean
  max_bytes: number
  omitted_bytes: number
  content: string
  authority: 'RAW_OBSERVED_DIAGNOSTIC'
}

export interface BuildFailureView {
  attempt: BuildAttemptView
  diagnostics: BuildAttemptView['diagnostics']
  evidence_correlations: Array<{diagnostic_id: string; resolution: string;
    canonical_entities: Array<{id: string}>; limitations: string[]}>
  root_cause_candidates: Array<{diagnostic_id: string; hypothesis: string;
    authority: string; supporting_evidence: string[]}>
  repair_candidates: Array<{id: string; kind: string; description: string;
    authority: string; status: string}>
  verification: {status: string; verified: boolean; test_run_id: string | null}
  limitations: string[]
}

export function listBuildAttempts(changeId: string): Promise<{attempts: BuildAttemptView[]}> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/build/attempts`)
}

export function getBuildFailure(changeId: string, attemptId: string): Promise<BuildFailureView> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/build/attempts/${encodeURIComponent(attemptId)}`)
}

export function getBuildArtifact(changeId: string, attemptId: string,
  kind: 'STDOUT' | 'STDERR', artifactId: string, expectedDigest: string,
  diagnosticId?: string): Promise<BuildArtifactView> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/build/attempts/${encodeURIComponent(attemptId)}`
    + `/artifacts/${kind}${qs({artifact_id: artifactId, expected_digest: expectedDigest,
      diagnostic_id: diagnosticId, max_bytes: 8192})}`)
}

export function getBuildDiagnosticSource(changeId: string, attemptId: string,
  diagnosticId: string, request: {revision: string; path: string; start_line: number;
    end_line: number}): Promise<{status: 'RESOLVED'; content: string;
      source_revision: string; diagnostic_id: string; source_sha256: string}> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/build/attempts/${encodeURIComponent(attemptId)}`
    + `/diagnostics/${encodeURIComponent(diagnosticId)}/source${qs(request)}`)
}

export interface ExecutionReceiptView {
  execution_id: string
  kind: 'BUILD' | 'TEST'
  profile_id: string
  profile_version: string
  source_revision: string | null
  result: 'PASS' | 'FAIL' | 'ERROR' | 'TIMEOUT' | 'CANCELLED' | 'UNKNOWN'
  termination_reason: string
  test_run_id: string | null
  started_ns: number
  duration_ms: number
  stdout_truncated: boolean
  stderr_truncated: boolean
  runner_identity: string
}

export interface TestRunReceiptView {
  test_run_id: string
  execution_id: string
  result: 'PASS' | 'FAIL'
  test_suite_id: string
  source_revision: string
  authority: 'SERVER_EXECUTION'
}

export function listChangeExecutions(changeId: string): Promise<{executions: ExecutionReceiptView[]}> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/executions`)
}

export function getChangeTestRun(changeId: string, executionId: string): Promise<TestRunReceiptView> {
  return apiGet(`/design-changes/${encodeURIComponent(changeId)}/executions/${encodeURIComponent(executionId)}/test-run`)
}
