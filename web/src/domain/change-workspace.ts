// Production Change Workspace boundary. Unknown/missing values never acquire
// truth or policy meaning from rendering code.
export type Freshness = 'CURRENT' | 'STALE' | 'UNKNOWN' | 'UNAVAILABLE'
export type ComparisonStatus =
  | 'MATCHED' | 'MISSING_IMPLEMENTATION'
  | 'UNEXPECTED_IMPLEMENTATION' | 'UNKNOWN'

export interface ResultBinding {
  baseline_revision: string
  design_revision: string
  actual_revision: string | null
  scope_digest: string
  scope: Record<string, unknown>
  run_identity?: string
  rule_version?: string | Record<string, string>
}

export interface ComparisonRow {
  status: ComparisonStatus
  claim: { kind: string | null; source: string | null; target: string | null }
  canonical_evidence_ref: string | null
}

export interface ComparisonProjection {
  comparison_identity: string | null
  outcome: ComparisonStatus
  rows: ComparisonRow[]
  negative_receipt: {
    searched_domain: Record<string, unknown> | null
    coverage: string
    certificate_refs?: string[]
    certificates: Array<{
      id: string; analyzer: string; provider_version: string
      analyzer_run_id: string; canonical_revision: string
      subject: string; relation_kind: string; translation_unit: string
      build_context: string; completeness: string
      included_domain: Record<string, unknown>; excluded_domain: string[]
      unsupported_constructs: string[]
    }>
    limitations: string[]
    observed_positive_evidence_refs: string[]
    rule_version: string | null
  } | null
}

export interface SourceSpanProjection {
  file: string
  path: string
  start_line: number
  start_column: number | null
  end_line: number
  end_column: number | null
  span_kind: 'ENTITY' | 'EVIDENCE_LOCATION'
  precision: 'COLUMN' | 'LINE'
  revision: string
  subject: string
  entity_type: 'node' | 'edge'
  evidence_ref: string | null
}

export interface EvidenceProjection {
  entity_type: 'node' | 'edge'
  entity_id: string
  canonical_revision: string
  authority: 'CANONICAL_CANDIDATE'
  support_status: 'ADMITTED' | 'LEGACY_UNKNOWN'
  truth_class: 'OBSERVED' | 'RESOLVED' | 'INFERRED' | 'HEURISTIC' | 'UNKNOWN'
  target_resolution: 'EXACT' | 'CANDIDATE_SET' | 'UNKNOWN'
  coverage: 'COMPLETE' | 'PARTIAL' | 'UNKNOWN'
  execution_modality: 'MUST' | 'MAY' | 'UNKNOWN'
  provider: string[]
  support_receipts: Array<{
    support_receipt_id: string
    canonical_revision: string
    canonical_fact_id: string
    canonical_fact_type: 'node' | 'edge'
    admission_id: string
    evidence_id: string
    lane_id: string
    producer: string
    producer_version: string
    truth_class: string
    target_resolution: string
    coverage: string
    execution_modality: string
    provenance: Record<string, unknown>
    limitations: string[]
    reuse_source_receipt_id: string | null
  }>
  provenance: Array<{source: string | null; location: Record<string, unknown>; payload: Record<string, unknown>}>
  supporting_evidence_refs: string[]
  source_span: SourceSpanProjection | null
  limitations: string[]
}

export interface RelationAlignmentProjection {
  relation_identity: {
    relation_id: string
    relation_kind: string
    source: string
    target: string | null
    semantic_scope: Record<string, unknown>
    design_revision: string
    canonical_revision: string
    runtime_run_scopes: string[]
  }
  design: {
    state: 'EXPECTED' | 'NOT_EXPECTED' | 'UNSPECIFIED'
    design_revision: string
    support: Record<string, unknown>[]
  }
  static: {
    state: 'PRESENT' | 'MISSING' | 'UNKNOWN'
    canonical_revision: string
    support_receipts: Record<string, unknown>[]
    coverage_certificate: Record<string, unknown> | null
    absence_domain: Record<string, unknown> | null
    reason: string | null
    canonical_edge_id: string | null
  }
  runtime: Array<{
    state: 'OBSERVED' | 'NOT_OBSERVED' | 'UNMEASURED'
    run_id: string
    workload_id: string | null
    trace_window: Record<string, unknown> | null
    provider: string | null
    binary_identity: {
      sha256: string | null
      build_identity: string | null
      compiler: Record<string, unknown> | null
      instrumentation_identity: string | null
    }
    runtime_coverage_receipt_id: string | null
    evidence_refs: string[]
    limitations: string[]
  }>
  alignment: {
    results: string[]
    expected_actual: string
    contradiction_audit: Record<string, unknown> | null
  }
  evidence_refs: string[]
}

interface ArtifactBase {
  id: string
  binding: ResultBinding | null
  freshness: Freshness
  raw_diagnostic: null
}

export type ArtifactProjection =
  | (ArtifactBase & {kind: 'DRC'; result: {
      status: 'PASS' | 'FAIL'; rule_version: string | null; finding_count: number
    }})
  | (ArtifactBase & {kind: 'COMPARISON'; result: ComparisonProjection})
  | (ArtifactBase & {kind: 'LVS'; result: {
      status: string; reason: string | null; diff_count: number
    }})
  | (ArtifactBase & {kind: 'VERIFICATION'; result: {
      criteria: Array<{criterion_id: string; status: string; evidence_refs: string[]}>
    }})
  | (ArtifactBase & {kind: 'CLOSE'; result: {
      status: 'CLOSED'; verification_evidence_count: number
    }})
  | (ArtifactBase & {kind: 'TESTS'; result: {
      status: 'PASS' | 'FAIL'; test_identity: string; run_identity: string
      evidence_refs: string[]
    }})
  | (ArtifactBase & {kind: 'APPROVAL'; result: {
      status: 'APPROVE' | 'REJECT'; approver_identity: string; policy_version: string
    }})

export interface SourceProjection {
  resolver: 'strict_identity_required'
  fallback: 'FORBIDDEN'
}

export interface ActivityProjection {
  reindex: {
    status: 'not_requested' | 'running' | 'failed' | 'completed' | 'stale_completed'
    observation_status: 'previous' | 'pending' | 'recorded'
    job_id: string | null
    run_receipt_id: string | null
    run_identity: string | null
    produced_canonical_revision: string | null
    adoption_status: 'ADOPTED' | 'PENDING' | 'UNAVAILABLE'
  }
  receipts: Array<{
    id: string; command: string; actor: string; state_after: string
    digest: string; created_at: number; binding: ResultBinding | null
  }>
}

export interface EligibilityProjection {
  change_id: string
  change_version: number
  policy_version: string
  subject_binding: ResultBinding
  authorization: 'UNAVAILABLE' | 'VERIFIED'
  authorization_requirement?: string
  authorization_identity?: string | null
  approver_identity: string | null
  allowed_actions: string[]
  denied_actions: string[]
  actions: Record<string, {
    allowed: boolean; denial_reasons: string[]; required_evidence: string[]
  }>
}

export interface ChangeWorkspaceProjection {
  schema_version: 'change-workspace/1'
  change: {
    id: string; repo_id: string; intent: string; state: string; version: number
    scope: Record<string, unknown>; base_canonical_revision: string
    acceptance_criteria: Array<{id: string; kind: string; required: boolean; description: string}>
  }
  design: {
    revision_id: string; authority: 'DESIGN_ANNOTATION'
    expected_claims: Array<{id: string | null; kind: string | null;
      source: string | null; target: string | null; justification: string | null}>
    architecture_proposal_ref: {identity: string; revision: string} | null
    flow_model_ref: {identity: string; revision: string} | null
  }
  current_binding: ResultBinding
  artifacts: ArtifactProjection[]
  alignments: RelationAlignmentProjection[]
  evidence: EvidenceProjection[]
  activity: ActivityProjection
  eligibility: EligibilityProjection
  source: SourceProjection
  freshness: Record<string, Freshness>
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function assert(condition: unknown, field: string): asserts condition {
  if (!condition) throw new Error(`Invalid Change Workspace projection: ${field}`)
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}
function records(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every(record)
}
const fresh = new Set(['CURRENT', 'STALE', 'UNKNOWN', 'UNAVAILABLE'])
const kinds = new Set(['DRC', 'COMPARISON', 'LVS', 'VERIFICATION', 'CLOSE', 'TESTS', 'APPROVAL'])
const statuses = new Set(['MATCHED', 'MISSING_IMPLEMENTATION',
  'UNEXPECTED_IMPLEMENTATION', 'UNKNOWN'])
function validBinding(value: unknown): value is ResultBinding {
  return record(value)
    && typeof value.baseline_revision === 'string'
    && typeof value.design_revision === 'string'
    && (value.actual_revision === null || typeof value.actual_revision === 'string')
    && typeof value.scope_digest === 'string' && record(value.scope)
}

export function validateChangeWorkspace(value: unknown): ChangeWorkspaceProjection {
  assert(record(value) && value.schema_version === 'change-workspace/1', 'schema_version')
  assert(record(value.change) && typeof value.change.id === 'string'
    && typeof value.change.repo_id === 'string' && typeof value.change.intent === 'string'
    && typeof value.change.state === 'string' && typeof value.change.version === 'number'
    && typeof value.change.base_canonical_revision === 'string'
    && record(value.change.scope) && Array.isArray(value.change.acceptance_criteria), 'change')
  assert(record(value.design) && typeof value.design.revision_id === 'string'
    && value.design.authority === 'DESIGN_ANNOTATION'
    && Array.isArray(value.design.expected_claims), 'design')
  assert(validBinding(value.current_binding), 'current_binding')
  assert(Array.isArray(value.artifacts), 'artifacts')
  for (const artifact of value.artifacts) {
    assert(record(artifact) && typeof artifact.id === 'string'
      && kinds.has(String(artifact.kind)) && fresh.has(String(artifact.freshness))
      && (artifact.binding === null || validBinding(artifact.binding))
      && record(artifact.result) && artifact.raw_diagnostic === null, 'artifact')
    if (artifact.kind === 'COMPARISON') {
      assert(statuses.has(String(artifact.result.outcome))
        && (artifact.result.comparison_identity === null
          || typeof artifact.result.comparison_identity === 'string')
        && Array.isArray(artifact.result.rows), 'comparison')
      for (const row of artifact.result.rows) {
        assert(record(row) && statuses.has(String(row.status)) && record(row.claim),
          'comparison.row')
      }
      const receipt = artifact.result.negative_receipt
      assert(receipt === null || (record(receipt)
        && typeof receipt.coverage === 'string'
        && Array.isArray(receipt.certificates)
        && receipt.certificates.every((cert: unknown) => record(cert)
          && typeof cert.id === 'string' && typeof cert.analyzer === 'string'
          && typeof cert.analyzer_run_id === 'string'
          && typeof cert.relation_kind === 'string'
          && typeof cert.build_context === 'string'
          && strings(cert.excluded_domain)
          && strings(cert.unsupported_constructs))
        && strings(receipt.limitations)
        && strings(receipt.observed_positive_evidence_refs)), 'comparison.negative_receipt')
    } else if (artifact.kind === 'DRC') {
      assert(['PASS', 'FAIL'].includes(String(artifact.result.status))
        && typeof artifact.result.finding_count === 'number', 'drc')
    } else if (artifact.kind === 'LVS') {
      assert(typeof artifact.result.status === 'string'
        && typeof artifact.result.diff_count === 'number', 'lvs')
    } else if (artifact.kind === 'VERIFICATION') {
      assert(Array.isArray(artifact.result.criteria)
        && artifact.result.criteria.every((item: unknown) => record(item)
          && typeof item.criterion_id === 'string'
          && typeof item.status === 'string' && strings(item.evidence_refs)), 'verification')
    } else if (artifact.kind === 'CLOSE') {
      assert(artifact.result.status === 'CLOSED'
        && typeof artifact.result.verification_evidence_count === 'number', 'close')
    } else if (artifact.kind === 'TESTS') {
      assert(['PASS', 'FAIL'].includes(String(artifact.result.status))
        && typeof artifact.result.test_identity === 'string'
        && typeof artifact.result.run_identity === 'string'
        && strings(artifact.result.evidence_refs), 'tests')
    } else if (artifact.kind === 'APPROVAL') {
      assert(['APPROVE', 'REJECT'].includes(String(artifact.result.status))
        && typeof artifact.result.approver_identity === 'string'
        && typeof artifact.result.policy_version === 'string', 'approval')
    }
  }
  assert(Array.isArray(value.alignments), 'alignments')
  for (const item of value.alignments) {
    assert(record(item) && record(item.relation_identity)
      && typeof item.relation_identity.relation_id === 'string'
      && typeof item.relation_identity.relation_kind === 'string'
      && typeof item.relation_identity.source === 'string'
      && (item.relation_identity.target === null
        || typeof item.relation_identity.target === 'string')
      && record(item.relation_identity.semantic_scope)
      && typeof item.relation_identity.design_revision === 'string'
      && typeof item.relation_identity.canonical_revision === 'string'
      && strings(item.relation_identity.runtime_run_scopes)
      && record(item.design)
      && ['EXPECTED', 'NOT_EXPECTED', 'UNSPECIFIED'].includes(String(item.design.state))
      && typeof item.design.design_revision === 'string'
      && records(item.design.support)
      && record(item.static)
      && ['PRESENT', 'MISSING', 'UNKNOWN'].includes(String(item.static.state))
      && typeof item.static.canonical_revision === 'string'
      && records(item.static.support_receipts)
      && (item.static.coverage_certificate === null
        || record(item.static.coverage_certificate))
      && (item.static.absence_domain === null || record(item.static.absence_domain))
      && (item.static.reason === null || typeof item.static.reason === 'string')
      && Array.isArray(item.runtime)
      && item.runtime.every((run: unknown) => record(run)
        && ['OBSERVED', 'NOT_OBSERVED', 'UNMEASURED'].includes(String(run.state))
        && typeof run.run_id === 'string'
        && (run.workload_id === null || typeof run.workload_id === 'string')
        && (run.trace_window === null || record(run.trace_window))
        && (run.provider === null || typeof run.provider === 'string')
        && record(run.binary_identity)
        && (run.binary_identity.sha256 === null
          || typeof run.binary_identity.sha256 === 'string')
        && (run.binary_identity.build_identity === null
          || typeof run.binary_identity.build_identity === 'string')
        && (run.binary_identity.compiler === null
          || record(run.binary_identity.compiler))
        && (run.binary_identity.instrumentation_identity === null
          || typeof run.binary_identity.instrumentation_identity === 'string')
        && (run.runtime_coverage_receipt_id === null
          || typeof run.runtime_coverage_receipt_id === 'string')
        && strings(run.evidence_refs) && strings(run.limitations))
      && record(item.alignment) && strings(item.alignment.results)
      && typeof item.alignment.expected_actual === 'string'
      && (item.alignment.contradiction_audit === null
        || record(item.alignment.contradiction_audit))
      && strings(item.evidence_refs), 'alignment')
  }
  assert(Array.isArray(value.evidence), 'evidence')
  for (const evidence of value.evidence) {
    assert(record(evidence) && ['node', 'edge'].includes(String(evidence.entity_type))
      && typeof evidence.entity_id === 'string'
      && typeof evidence.canonical_revision === 'string'
      && evidence.authority === 'CANONICAL_CANDIDATE'
      && ['ADMITTED', 'LEGACY_UNKNOWN'].includes(String(evidence.support_status))
      && ['OBSERVED', 'RESOLVED', 'INFERRED', 'HEURISTIC', 'UNKNOWN']
        .includes(String(evidence.truth_class))
      && ['EXACT', 'CANDIDATE_SET', 'UNKNOWN'].includes(String(evidence.target_resolution))
      && ['COMPLETE', 'PARTIAL', 'UNKNOWN'].includes(String(evidence.coverage))
      && ['MUST', 'MAY', 'UNKNOWN'].includes(String(evidence.execution_modality))
      && strings(evidence.provider) && Array.isArray(evidence.provenance)
      && Array.isArray(evidence.support_receipts)
      && (evidence.support_status === 'ADMITTED'
        ? evidence.support_receipts.length > 0 : evidence.support_receipts.length === 0)
      && evidence.support_receipts.every((receipt: unknown) =>
        record(receipt) && typeof receipt.support_receipt_id === 'string'
        && receipt.canonical_revision === evidence.canonical_revision
        && receipt.canonical_fact_id === evidence.entity_id
        && receipt.canonical_fact_type === evidence.entity_type
        && typeof receipt.admission_id === 'string'
        && typeof receipt.evidence_id === 'string'
        && typeof receipt.lane_id === 'string'
        && typeof receipt.producer === 'string'
        && typeof receipt.producer_version === 'string'
        && ['OBSERVED', 'RESOLVED', 'INFERRED', 'HEURISTIC', 'UNKNOWN'].includes(String(receipt.truth_class))
        && ['EXACT', 'CANDIDATE_SET', 'UNKNOWN'].includes(String(receipt.target_resolution))
        && ['COMPLETE', 'PARTIAL', 'UNKNOWN'].includes(String(receipt.coverage))
        && ['MUST', 'MAY', 'UNKNOWN'].includes(String(receipt.execution_modality))
        && record(receipt.provenance) && strings(receipt.limitations))
      && strings(evidence.supporting_evidence_refs)
      && (evidence.source_span === null || (record(evidence.source_span)
        && typeof evidence.source_span.path === 'string'
        && evidence.source_span.file === evidence.source_span.path
        && Number.isInteger(evidence.source_span.start_line)
        && Number.isInteger(evidence.source_span.end_line)
        && Number(evidence.source_span.start_line) > 0
        && Number(evidence.source_span.end_line) >= Number(evidence.source_span.start_line)
        && evidence.source_span.revision === evidence.canonical_revision
        && evidence.source_span.subject === evidence.entity_id
        && evidence.source_span.entity_type === evidence.entity_type
        && (evidence.source_span.evidence_ref === null
          || evidence.supporting_evidence_refs.includes(String(evidence.source_span.evidence_ref)))))
      && strings(evidence.limitations), 'evidence')
  }
  assert(record(value.activity) && record(value.activity.reindex)
    && Array.isArray(value.activity.receipts)
    && ['previous', 'pending', 'recorded'].includes(String(value.activity.reindex.observation_status))
    && ['not_requested', 'running', 'failed', 'completed', 'stale_completed']
      .includes(String(value.activity.reindex.status)), 'activity')
  assert(record(value.eligibility) && typeof value.eligibility.change_version === 'number'
    && strings(value.eligibility.allowed_actions)
    && strings(value.eligibility.denied_actions)
    && validBinding(value.eligibility.subject_binding)
    && ['UNAVAILABLE', 'VERIFIED'].includes(String(value.eligibility.authorization))
    && (value.eligibility.approver_identity === null
      || typeof value.eligibility.approver_identity === 'string')
    && record(value.eligibility.actions), 'eligibility')
  assert(record(value.source) && value.source.resolver === 'strict_identity_required'
    && value.source.fallback === 'FORBIDDEN', 'source')
  assert(record(value.freshness)
    && Object.values(value.freshness).every((item) => fresh.has(String(item))), 'freshness')
  return value as unknown as ChangeWorkspaceProjection
}
