// DESIGN-LIFECYCLE0 shared Human/Agent substrate.  These statuses describe
// workflow state; they never replace evidence truth/coverage/resolution.
export type DesignChangeState =
  | 'OPEN' | 'PLANNED' | 'IMPLEMENTING' | 'EVIDENCE_MATCHED'
  | 'VERIFIED' | 'CLOSED' | 'ABANDONED'

export interface DesignRef {
  identity: string
  revision: string
}

export interface AcceptanceCriterion {
  id: string
  kind: 'structural' | 'semantic' | 'test' | 'runtime' | 'manual'
  required: boolean
  description?: string
}

export interface DesignRevision {
  id: string
  authority: 'DESIGN_ANNOTATION'
  architecture_proposal_ref: DesignRef | null
  flow_model_ref: DesignRef | null
  expected_changes: Array<Record<string, unknown>>
  digest: string
}

export interface DesignChange {
  id: string
  repo_id: string
  base_canonical_revision: string
  intent: string
  scope: Record<string, unknown>
  acceptance_criteria: AcceptanceCriterion[]
  design_revision: DesignRevision
  evidence_brief: Record<string, unknown>
  state: DesignChangeState
  design_drc: Record<string, unknown> | null
  implementation: Record<string, unknown> | null
  expected_actual: Record<string, unknown> | null
  lvs_result: Record<string, unknown> | null
  verification: Array<Record<string, unknown>>
  close_receipt: Record<string, unknown> | null
  version: number
}

export interface DesignCommand {
  command: 'plan' | 'begin_implementation' | 'record_reindex' | 'run_reindex'
    | 'evaluate_expected_actual' | 'verify' | 'close' | 'abandon'
    | 'design_mutation'
  actor: string
  [key: string]: unknown
}
