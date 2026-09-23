<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { applyDesignCommand, approveDesignChange, bootstrapLocalOwner, getBuildArtifact, getBuildDiagnosticSource, getChangeWorkspace, getDesignChange, getGitCollaboration, getLocalOwnerSession, getBuildFailure, getChangeTestRun, listBuildAttempts, listChangeExecutions, logoutLocalOwner, resolveBoundSource } from '../api/design-lifecycle'
import type { BuildArtifactView, BuildAttemptView, BuildFailureView, ExecutionReceiptView, GitCollaborationProjection, LocalOwnerSession, TestRunReceiptView } from '../api/design-lifecycle'
import type { ArtifactProjection, ChangeWorkspaceProjection, EvidenceProjection } from '../domain/change-workspace'

const route = useRoute()
const workspace = ref<ChangeWorkspaceProjection | null>(null)
const error = ref('')
const loading = ref(false)
const page = ref<'overview' | 'design' | 'compare' | 'checks' | 'build' | 'collaboration' | 'verify' | 'activity'>('overview')
const buildAttempts = ref<BuildAttemptView[]>([])
const buildFailure = ref<BuildFailureView | null>(null)
const executionRuns = ref<ExecutionReceiptView[]>([])
const selectedExecution = ref<ExecutionReceiptView | null>(null)
const selectedTestRun = ref<TestRunReceiptView | null>(null)
const buildError = ref('')
const diagnosticSource = ref<string | null>(null)
const diagnosticSourceError = ref('')
const rawDiagnosticId = ref<string | null>(null)
const evidenceDiagnosticId = ref<string | null>(null)
const rawStdout = ref<BuildArtifactView | null>(null)
const rawStderr = ref<BuildArtifactView | null>(null)
const rawError = ref('')
const rawLoading = ref(false)
const rawPanel = ref<HTMLElement | null>(null)
const mode = ref<'AS-IS' | 'TO-BE' | 'ACTUAL' | 'DIFF'>('DIFF')
const selectedEvidenceKey = ref<{id: string; revision: string} | null>(null)
const sourceContent = ref<string | null>(null)
const sourceError = ref('')
const gitCollaboration = ref<GitCollaborationProjection | null>(null)
const ownerSession = ref<LocalOwnerSession | null>(null)
const ownerToken = ref('')
const ownerError = ref('')
const ownerResult = ref('')
const ownerBusy = ref(false)
const expectedChangesJson = ref('[]')
const writeBusy = ref(false)
const writeError = ref('')
const writeResult = ref('')

function expectedChanges(): Record<string, unknown>[] {
  const value: unknown = JSON.parse(expectedChangesJson.value)
  if (!Array.isArray(value) || !value.every((item) => item && typeof item === 'object' && !Array.isArray(item))) {
    throw new Error('TO-BE claims must be a JSON array of objects')
  }
  return value as Record<string, unknown>[]
}

async function saveDesignAndRunDrc(): Promise<void> {
  if (!workspace.value) return
  writeBusy.value = true
  writeError.value = ''
  writeResult.value = 'Design save requested; awaiting server result'
  try {
    const prior = workspace.value
    const result = await applyDesignCommand(prior.change.id, {
      command: 'plan', actor: 'human:ui', change_version: prior.change.version,
      expected_design_revision: prior.design.revision_id,
      expected_changes: expectedChanges(),
    })
    writeResult.value = `DesignRevision ${result.design_revision.id} · DRC ${result.design_drc?.acceptable ? 'PASS' : 'FAIL'} · server version ${result.version}`
    await load()
  } catch (err) {
    writeError.value = err instanceof Error ? err.message : String(err)
    await load()
  } finally { writeBusy.value = false }
}

async function beginImplementation(): Promise<void> {
  if (!workspace.value) return
  writeBusy.value = true
  writeError.value = ''
  writeResult.value = 'Implementation request submitted; awaiting server result'
  try {
    const prior = workspace.value
    const result = await applyDesignCommand(prior.change.id, {
      command: 'begin_implementation', actor: 'human:ui',
      change_version: prior.change.version,
      expected_design_revision: prior.design.revision_id,
      expected_touched_scope: prior.change.scope,
    })
    writeResult.value = `Begin Implementation · ${result.state} · server version ${result.version}`
    await load()
  } catch (err) {
    writeError.value = err instanceof Error ? err.message : String(err)
    await load()
  } finally { writeBusy.value = false }
}

async function refreshOwner(): Promise<void> {
  try { ownerSession.value = await getLocalOwnerSession() }
  catch { ownerSession.value = null }
}

async function authenticateOwner(): Promise<void> {
  ownerBusy.value = true
  ownerError.value = ''
  ownerResult.value = ''
  try {
    ownerSession.value = await bootstrapLocalOwner(ownerToken.value)
    ownerToken.value = ''
    await load()
  } catch (err) {
    ownerError.value = err instanceof Error ? err.message : String(err)
    await refreshOwner()
  }
  finally { ownerBusy.value = false }
}

async function decideApproval(decision: 'APPROVE' | 'REJECT'): Promise<void> {
  if (!workspace.value || !ownerSession.value?.authenticated
      || !workspace.value.eligibility.actions.approve?.allowed) return
  ownerBusy.value = true
  ownerError.value = ''
  try {
    const receipt = await approveDesignChange(workspace.value.change.id, {
      decision, expected_version: workspace.value.change.version,
      expected_design_revision: workspace.value.design.revision_id,
    })
    ownerResult.value = `${decision} · receipt ${receipt.id}`
    await load()
  } catch (err) {
    ownerError.value = err instanceof Error ? err.message : String(err)
    await load()
  }
  finally { ownerBusy.value = false }
}

async function revokeOwner(): Promise<void> {
  ownerBusy.value = true
  ownerError.value = ''
  try { await logoutLocalOwner(); await load() }
  catch (err) { ownerError.value = err instanceof Error ? err.message : String(err) }
  finally { ownerBusy.value = false }
}

async function load(): Promise<void> {
  const id = String(route.params.id ?? '')
  if (!id) return
  loading.value = true
  error.value = ''
  try {
    workspace.value = await getChangeWorkspace(id)
    gitCollaboration.value = await getGitCollaboration(id)
    expectedChangesJson.value = JSON.stringify((await getDesignChange(id)).design_revision.expected_changes, null, 2)
    buildAttempts.value = (await listBuildAttempts(id)).attempts
    executionRuns.value = (await listChangeExecutions(id)).executions
    buildFailure.value = null
    selectedExecution.value = null
    selectedTestRun.value = null
    await refreshOwner()
  }
  catch (err) { error.value = err instanceof Error ? err.message : String(err) }
  finally { loading.value = false }
}

async function selectBuild(attemptId: string): Promise<void> {
  buildError.value = ''
  diagnosticSource.value = null
  diagnosticSourceError.value = ''
  rawDiagnosticId.value = null
  evidenceDiagnosticId.value = null
  rawStdout.value = null
  rawStderr.value = null
  rawError.value = ''
  try { buildFailure.value = await getBuildFailure(String(route.params.id), attemptId) }
  catch (err) { buildError.value = err instanceof Error ? err.message : String(err) }
}

async function openRawDiagnostic(diagnostic: BuildFailureView['diagnostics'][number]): Promise<void> {
  const failure = buildFailure.value
  if (!failure || !workspace.value) return
  rawDiagnosticId.value = diagnostic.id
  rawStdout.value = null
  rawStderr.value = null
  rawError.value = ''
  rawLoading.value = true
  try {
    const attempt = failure.attempt
    const changeId = workspace.value.change.id
    const [stderr, stdout] = await Promise.all([
      getBuildArtifact(changeId, attempt.attempt_id, 'STDERR', attempt.stderr_ref,
        attempt.stderr_sha256, diagnostic.id),
      getBuildArtifact(changeId, attempt.attempt_id, 'STDOUT', attempt.stdout_ref,
        attempt.stdout_sha256),
    ])
    if (buildFailure.value?.attempt.attempt_id !== attempt.attempt_id
        || rawDiagnosticId.value !== diagnostic.id) return
    rawStderr.value = stderr
    rawStdout.value = stdout
    await nextTick()
    rawPanel.value?.scrollIntoView({block: 'start'})
  } catch (err) {
    rawError.value = err instanceof Error ? err.message : String(err)
  } finally { rawLoading.value = false }
}

async function openBuildDiagnostic(diagnostic: BuildFailureView['diagnostics'][number]): Promise<void> {
  const span = diagnostic.source_span
  if (!workspace.value || !buildFailure.value || !span) return
  diagnosticSource.value = null
  diagnosticSourceError.value = ''
  try {
    const result = await getBuildDiagnosticSource(workspace.value.change.id,
      buildFailure.value.attempt.attempt_id, diagnostic.id,
      {revision: buildFailure.value.attempt.source_revision, path: span.path,
       start_line: span.start_line, end_line: span.end_line ?? span.start_line})
    diagnosticSource.value = result.content
  } catch (err) {
    diagnosticSourceError.value = err instanceof Error ? err.message : String(err)
  }
}

async function selectExecution(execution: ExecutionReceiptView): Promise<void> {
  selectedExecution.value = execution
  selectedTestRun.value = execution.test_run_id
    ? await getChangeTestRun(String(route.params.id), execution.execution_id) : null
}
watch(() => route.params.id, load, { immediate: true })

const artifacts = computed(() => workspace.value?.artifacts ?? [])
const comparison = computed(() => [...artifacts.value].reverse().find(
  (item): item is Extract<ArtifactProjection, {kind: 'COMPARISON'}> => item.kind === 'COMPARISON'))
const comparisonResult = computed(() => comparison.value?.result)
const selectedEvidence = computed(() => workspace.value?.evidence.find(
  (item) => item.entity_id === selectedEvidenceKey.value?.id
    && item.canonical_revision === selectedEvidenceKey.value?.revision) ?? null)
const baseEvidence = computed(() => workspace.value?.evidence.filter(
  (item) => item.canonical_revision === workspace.value?.change.base_canonical_revision) ?? [])

function selectEvidence(id: string | null, revision?: string | null): void {
  selectedEvidenceKey.value = id && revision ? { id, revision } : null
  sourceContent.value = null
  sourceError.value = ''
}

async function openAlignmentSource(edgeId: string, revision: string): Promise<void> {
  const evidence = workspace.value?.evidence.find((item) =>
    item.entity_id === edgeId && item.canonical_revision === revision)
  if (!evidence) {
    sourceError.value = 'SOURCE_UNRESOLVED · alignment canonical relation is not projected'
    return
  }
  selectEvidence(edgeId, revision)
  await openSource(evidence)
}

async function openSource(evidence: EvidenceProjection): Promise<void> {
  const span = evidence.source_span
  const path = span?.path
  const line = span?.start_line
  const end = span?.end_line
  const evidenceRef = span?.evidence_ref
  if (!workspace.value || typeof path !== 'string' || typeof line !== 'number'
      || typeof end !== 'number' || !evidenceRef) {
    sourceError.value = 'SOURCE_UNRESOLVED · 缺少已绑定的 SourceSpan / evidence ref'
    return
  }
  try {
    const resolved = await resolveBoundSource(workspace.value.change.id, {
      revision: evidence.canonical_revision, entity_type: evidence.entity_type,
      entity_id: evidence.entity_id, evidence_ref: evidenceRef,
      path, start_line: line, end_line: end,
    })
    sourceContent.value = resolved.content
    sourceError.value = ''
  } catch (err) {
    sourceContent.value = null
    sourceError.value = err instanceof Error ? err.message : 'SOURCE_UNRESOLVED'
  }
}

function claimLabel(claim: {kind: string | null; source: string | null; target: string | null}): string {
  return `${claim.source ?? 'UNKNOWN'} —${claim.kind ?? '?'}→ ${claim.target ?? 'UNKNOWN'}`
}
</script>

<template>
  <div class="change-shell">
    <aside class="change-nav" aria-label="Change navigation">
      <RouterLink to="/">◈ rintel</RouterLink>
      <p class="label">CHANGE WORKSPACE</p>
      <button v-for="item in ([['overview', '概览 / Overview'], ['design', '设计 / TO-BE'],
        ['compare', '对账 / Compare'], ['checks', '检查 / DRC & LVS'], ['build', '构建 / Build'],
        ['collaboration', '工作区与合并'],
        ['verify', '验证与关闭'], ['activity', '活动与 Agent 交接']] as const)"
        :key="item[0]" :aria-current="page === item[0] ? 'page' : undefined"
        @click="page = item[0]">{{ item[1] }}</button>
    </aside>
    <main class="change-main">
      <p v-if="loading && !workspace">载入真实 Change Workspace…</p>
      <p v-else-if="error" role="alert">{{ error }}</p>
      <template v-else-if="workspace">
        <header>
          <p class="label">{{ workspace.change.id }} · {{ workspace.change.repo_id }}</p>
          <h1>{{ workspace.change.intent }}</h1>
          <p>状态 {{ workspace.change.state }} · version {{ workspace.change.version }}</p>
          <div class="identity-bar">
            <span>AS-IS {{ workspace.change.base_canonical_revision }}</span>
            <span>TO-BE {{ workspace.design.revision_id }}</span>
            <span>ACTUAL {{ workspace.current_binding.actual_revision ?? 'UNAVAILABLE' }}</span>
            <span>Subject scope: {{ workspace.current_binding.scope_digest.slice(0, 12) }}</span>
            <span>Selection: {{ selectedEvidenceKey?.id ?? 'none' }}（不改变 scope）</span>
          </div>
        </header>
        <section v-if="page === 'overview'" class="cards" aria-label="四问 Overview">
          <article><h2>01 / 现在是什么</h2>
            <p>基线 {{ workspace.change.base_canonical_revision }}；已投影 {{ baseEvidence.length }} 条证据。</p>
            <button @click="page = 'compare'; mode = 'AS-IS'">查看 AS-IS</button></article>
          <article><h2>02 / 准备改什么</h2>
            <p>{{ workspace.design.expected_claims.length }} 条 TO-BE claim；authority = {{ workspace.design.authority }}。</p>
            <button @click="page = 'design'">查看 TO-BE</button></article>
          <article><h2>03 / 实际改了什么</h2>
            <p>{{ workspace.current_binding.actual_revision ?? '没有新观察' }} · {{ workspace.freshness.ACTUAL }} · {{ workspace.activity.reindex.observation_status === 'recorded' ? '新观察已记录' : '仅前次 canonical 观察' }}。</p>
            <p>比较 {{ comparisonResult?.outcome ?? 'UNAVAILABLE' }} · {{ comparison?.freshness ?? 'UNAVAILABLE' }}</p>
            <button @click="page = 'compare'; mode = 'ACTUAL'">查看 ACTUAL</button></article>
          <article><h2>04 / 还没验证什么</h2>
            <p>Tests {{ workspace.freshness.TESTS }} · Verification {{ workspace.freshness.VERIFICATION }} · Approval {{ workspace.freshness.APPROVAL }}</p>
            <button @click="page = 'verify'">查看门槛</button></article>
        </section>
        <section v-else-if="page === 'design'" class="panel">
          <h2>TO-BE · {{ workspace.design.revision_id }}</h2>
          <p>DESIGN_ANNOTATION；不会修改 canonical truth。</p>
          <p v-for="(claim, i) in workspace.design.expected_claims" :key="i">
            {{ claimLabel(claim) }} · {{ claim.justification ?? '无说明' }}
          </p>
          <p v-if="!workspace.design.expected_claims.length">尚无 TO-BE claim。</p>
          <h3>Edit TO-BE / Save DesignRevision / Run Design DRC</h3>
          <p>使用现有 DesignLifecycle plan 命令；保存与 DRC 在服务端同一事务完成。提交会带当前 version 和 DesignRevision 身份。</p>
          <label for="expected-changes">Expected claims JSON</label>
          <textarea id="expected-changes" v-model="expectedChangesJson" rows="12" spellcheck="false" />
          <button :disabled="writeBusy || !workspace.eligibility.actions.plan?.allowed"
            @click="saveDesignAndRunDrc">Save DesignRevision · Run Design DRC</button>
          <p v-if="!workspace.eligibility.actions.plan?.allowed">Server denial: {{ workspace.eligibility.actions.plan?.denial_reasons.join('；') }}</p>
          <p>DRC {{ artifacts.filter(a => a.kind === 'DRC').at(-1)?.result.status ?? 'UNAVAILABLE' }}</p>
          <button :disabled="writeBusy || !workspace.eligibility.actions.begin_implementation?.allowed"
            @click="beginImplementation">Begin Implementation</button>
          <p v-if="!workspace.eligibility.actions.begin_implementation?.allowed">Server denial: {{ workspace.eligibility.actions.begin_implementation?.denial_reasons.join('；') }}</p>
          <p v-if="writeError" role="alert">{{ writeError }}</p>
          <p v-if="writeResult" role="status">{{ writeResult }}</p>
        </section>
        <section v-else-if="page === 'compare'" class="panel">
          <h2>Expected vs Actual</h2>
          <p v-if="comparison">Receipt {{ comparison.id }} · {{ comparison.freshness }} ·
            bound D {{ comparison.binding?.design_revision ?? 'UNAVAILABLE' }} / E {{ comparison.binding?.actual_revision ?? 'UNAVAILABLE' }}</p>
          <div class="modes"><button v-for="plane in (['AS-IS', 'TO-BE', 'ACTUAL', 'DIFF'] as const)"
            :key="plane" :aria-pressed="mode === plane" @click="mode = plane">{{ plane }}</button></div>
          <p>当前视图 {{ mode }}；切换视图不改变 subject scope 或记录绑定。</p>
          <template v-if="mode === 'TO-BE' || mode === 'DIFF'">
            <h3>TO-BE · {{ workspace.design.revision_id }}</h3>
            <p v-for="(claim, i) in workspace.design.expected_claims" :key="i">{{ claimLabel(claim) }}</p>
          </template>
          <template v-if="mode === 'ACTUAL' || mode === 'DIFF'">
            <h3>ACTUAL · {{ comparison?.binding?.actual_revision ?? 'UNAVAILABLE' }}</h3>
            <p v-for="(row, i) in comparisonResult?.rows ?? []" :key="i">
              {{ row.status }} · {{ claimLabel(row.claim) }}
              <button v-if="row.canonical_evidence_ref" @click="selectEvidence(row.canonical_evidence_ref, comparison?.binding?.actual_revision)">证据链</button>
            </p>
            <p v-if="comparisonResult?.negative_receipt?.coverage === 'UNKNOWN'">
              未命中不是缺失证明；coverage UNKNOWN。
              {{ comparisonResult.negative_receipt.limitations.join('；') }}
            </p>
            <p v-if="comparisonResult?.negative_receipt?.coverage === 'COMPLETE'">
              MISSING_IMPLEMENTATION 仅由下列精确限定的 COMPLETE certificate 支撑。
            </p>
            <div v-for="certificate in comparisonResult?.negative_receipt?.certificates ?? []"
              :key="certificate.id">
              <p>CoverageCertificate {{ certificate.id }} · {{ certificate.completeness }}</p>
              <p>{{ certificate.analyzer }}@{{ certificate.provider_version }} · run {{ certificate.analyzer_run_id }}</p>
              <p>subject {{ certificate.subject }} · relation {{ certificate.relation_kind }} · TU {{ certificate.translation_unit }}</p>
              <p>build {{ certificate.build_context }} · revision {{ certificate.canonical_revision }}</p>
              <p>included {{ JSON.stringify(certificate.included_domain) }}</p>
              <p>excluded {{ certificate.excluded_domain.join('；') }} · unsupported {{ certificate.unsupported_constructs.join('；') || 'NONE' }}</p>
            </div>
          </template>
          <template v-if="mode === 'AS-IS'">
            <h3>AS-IS · {{ workspace.change.base_canonical_revision }}</h3>
            <button v-for="item in baseEvidence"
              :key="item.entity_id" @click="selectEvidence(item.entity_id, item.canonical_revision)">{{ item.entity_id }}</button>
          </template>
          <h3>Cross-layer alignment · 后端判定</h3>
          <p>Design 是意图；Static 是代码证据；Runtime 只说明指定执行中发生了什么。此处只展示后端结果，不从图上缺边推断 MISSING。</p>
          <p v-if="!workspace.alignments.length">尚无精确关系对齐记录。</p>
          <article v-for="item in workspace.alignments" :key="item.relation_identity.relation_id"
            class="alignment-row">
            <h4>{{ item.relation_identity.source }} —{{ item.relation_identity.relation_kind }}→
              {{ item.relation_identity.target ?? 'UNKNOWN target' }}</h4>
            <p>Relation {{ item.relation_identity.relation_id }} · D {{ item.relation_identity.design_revision }} ·
              C {{ item.relation_identity.canonical_revision }}</p>
            <p>Runtime source binding {{ item.relation_identity.semantic_scope.source_revision ?? 'UNKNOWN' }} ·
              binary binding {{ item.relation_identity.semantic_scope.binary_sha256 ?? 'UNKNOWN' }} ·
              build binding {{ item.relation_identity.semantic_scope.build_identity ?? 'UNKNOWN' }}</p>
            <p>Design {{ item.design.state }} · Static {{ item.static.state }} ·
              {{ item.alignment.results.join(' / ') || 'NO_DERIVED_RESULT' }}</p>
            <button v-if="item.static.canonical_edge_id"
              @click="openAlignmentSource(item.static.canonical_edge_id, item.relation_identity.canonical_revision)">
              查看 alignment source
            </button>
            <p>Expected-vs-Actual {{ item.alignment.expected_actual }} ·
              Static reason {{ item.static.reason ?? 'none' }}</p>
            <p v-if="item.static.coverage_certificate">
              Static absence certificate {{ item.static.coverage_certificate.id ?? 'UNKNOWN' }};
              bounded scope {{ JSON.stringify(item.static.absence_domain) }}
            </p>
            <p v-else-if="item.static.state === 'UNKNOWN'">Static absence not proven.</p>
            <p v-if="item.alignment.contradiction_audit">
              CROSS_LAYER_CONTRADICTION · audit {{ JSON.stringify(item.alignment.contradiction_audit) }}
            </p>
            <p v-if="!item.runtime.length">Runtime UNMEASURED · no applicable run scope.</p>
            <p v-for="run in item.runtime" :key="run.run_id">
              Runtime {{ run.state }} · {{ run.run_id }} · workload {{ run.workload_id ?? 'UNKNOWN' }} ·
              window {{ JSON.stringify(run.trace_window) }} · provider {{ run.provider ?? 'UNKNOWN' }} ·
              binary {{ run.binary_identity.sha256 ?? 'UNKNOWN' }} ·
              build {{ run.binary_identity.build_identity ?? 'UNKNOWN' }} ·
              runtime coverage {{ run.runtime_coverage_receipt_id ?? 'UNAVAILABLE' }} ·
              limitations {{ run.limitations.join('；') || 'none stated' }}
              <span v-if="run.state === 'NOT_OBSERVED'"> · 仅本次执行未见，不代表静态缺失或永不执行。</span>
            </p>
          </article>
        </section>
        <section v-else-if="page === 'checks'" class="panel">
          <h2>DRC / LVS</h2>
          <p v-for="item in artifacts.filter(a => a.kind === 'DRC' || a.kind === 'LVS')" :key="item.id">
            {{ item.kind }} · {{ item.id }} · {{ item.freshness }} · D {{ item.binding?.design_revision ?? 'UNAVAILABLE' }}
          </p>
        </section>
        <section v-else-if="page === 'build'" class="panel" aria-label="Build Recovery">
          <h2>Build · Attempts</h2>
          <p>编译器输出是 OBSERVED；关联与根因是推断；修复是 DESIGN_ANNOTATION。构建通过不等于测试或设计验证通过。</p>
          <h3>Execution · BUILD / TEST</h3>
          <p>Agent report ≠ ExecutionReceipt。REQUESTED / RUNNING 是未完成状态；只有服务端实际执行结束后才有不可变 receipt。</p>
          <p v-if="!executionRuns.length">UNAVAILABLE · 尚无受控执行记录。</p>
          <button v-for="run in executionRuns" :key="run.execution_id"
            @click="selectExecution(run)">
            {{ run.kind }} · {{ run.result }} · {{ run.profile_id }}@{{ run.profile_version }} · {{ run.execution_id }}
          </button>
          <p v-if="selectedExecution">{{ selectedExecution.kind }} {{ selectedExecution.result }} · {{ selectedExecution.termination_reason }} · input {{ selectedExecution.source_revision ?? 'UNKNOWN' }} · {{ selectedExecution.duration_ms }} ms · stdout truncated {{ selectedExecution.stdout_truncated }} · stderr truncated {{ selectedExecution.stderr_truncated }}</p>
          <p v-if="selectedTestRun">Trusted TEST · {{ selectedTestRun.result }} · {{ selectedTestRun.test_suite_id }} · {{ selectedTestRun.test_run_id }}</p>
          <p v-else-if="selectedExecution?.kind === 'TEST'">No trusted TestRunReceipt for this execution.</p>
          <h3>Build Recovery attempts</h3>
          <p v-if="!buildAttempts.length">UNAVAILABLE · 当前 datastore 无 BuildAttempt；需由服务端配置受限 build profile。</p>
          <button v-for="attempt in buildAttempts" :key="attempt.attempt_id"
            @click="selectBuild(attempt.attempt_id)">
            {{ attempt.attempt_id }} · exit {{ attempt.exit_status ?? 'UNKNOWN' }} · {{ attempt.verification_status }}
          </button>
          <p v-if="buildError" role="alert">{{ buildError }}</p>
          <template v-if="buildFailure">
            <h3>Diagnostics · compiler OBSERVED</h3>
            <article v-for="diagnostic in buildFailure.diagnostics" :key="diagnostic.id">
              <h4>Normalized · {{ diagnostic.id }}</h4>
              <p>{{ diagnostic.category }} · {{ diagnostic.source_span?.path ?? 'unknown file' }}:{{ diagnostic.source_span?.start_line ?? '?' }} · {{ diagnostic.message }}</p>
              <button :aria-label="`查看 diagnostic raw ${diagnostic.id}`"
                @click="openRawDiagnostic(diagnostic)">Raw</button>
              <button v-if="diagnostic.source_span" :aria-label="`查看 diagnostic source ${diagnostic.id}`"
                @click="openBuildDiagnostic(diagnostic)">Source · 查看 diagnostic source</button>
              <button :aria-label="`查看 diagnostic evidence ${diagnostic.id}`"
                @click="evidenceDiagnosticId = diagnostic.id">Evidence</button>
              <p v-if="evidenceDiagnosticId === diagnostic.id" v-for="item in buildFailure.evidence_correlations.filter(item => item.diagnostic_id === diagnostic.id)" :key="item.diagnostic_id">
                {{ item.resolution }} · {{ item.canonical_entities.map(entity => entity.id).join(', ') || 'no canonical match' }} · {{ item.limitations.join('; ') }}
              </p>
            </article>
            <section v-if="rawDiagnosticId" ref="rawPanel" aria-label="Raw compiler output">
              <h4>RAW OBSERVED DIAGNOSTIC · {{ rawDiagnosticId }}</h4>
              <p v-if="rawLoading">Loading bound raw artifacts…</p>
              <p v-if="rawError" role="alert">{{ rawError }}</p>
              <template v-for="artifact in [rawStderr, rawStdout]" :key="artifact?.artifact_id">
                <div v-if="artifact">
                  <h5>{{ artifact.kind }} · Compiler / linker original output</h5>
                  <p>Artifact {{ artifact.artifact_id }} · SHA-256 {{ artifact.sha256 }} · {{ artifact.size }} original bytes · {{ artifact.encoding }} · display truncated {{ artifact.truncated }} · capture truncated {{ artifact.capture_truncated }}</p>
                  <pre>{{ artifact.content }}</pre>
                </div>
              </template>
            </section>
            <p v-if="diagnosticSourceError" role="alert">{{ diagnosticSourceError }}</p>
            <pre v-if="diagnosticSource">{{ diagnosticSource }}</pre>
            <h3>Evidence · correlation</h3>
            <p v-for="item in buildFailure.evidence_correlations" :key="item.diagnostic_id">
              {{ item.resolution }} · {{ item.canonical_entities.map(entity => entity.id).join(', ') || 'no canonical match' }}
              {{ item.limitations.join('; ') }}
            </p>
            <h3>Root causes · Rintel INFERRED</h3>
            <p v-for="item in buildFailure.root_cause_candidates" :key="item.diagnostic_id">
              {{ item.hypothesis }} · {{ item.supporting_evidence.join(', ') }}
            </p>
            <h3>Repair candidates · DESIGN_ANNOTATION</h3>
            <p v-for="item in buildFailure.repair_candidates" :key="item.id">
              {{ item.kind }} · {{ item.description }} · {{ item.status }}
            </p>
            <h3>Verification</h3>
            <p>{{ buildFailure.verification.status }} · verified {{ buildFailure.verification.verified }} · trusted test {{ buildFailure.verification.test_run_id ?? 'NOT_RUN' }}</p>
          <p>Execution {{ buildFailure.attempt.execution_id ?? 'LEGACY_UNAVAILABLE' }} · previous {{ buildFailure.attempt.previous_attempt ?? 'none' }} · application {{ buildFailure.attempt.application_id ?? 'none' }}</p>
          </template>
        </section>
        <section v-else-if="page === 'collaboration'" class="panel" aria-label="Git collaboration">
          <h2>Workspaces · Agents · Merge Queue</h2>
          <p>Git 管理 commit、branch、worktree 与文本 merge；Rintel 显示身份、scope、证据和 eligibility。Worktree 不是安全 sandbox。</p>
          <p>Repository {{ gitCollaboration?.repository.repository_identity ?? gitCollaboration?.repository.status ?? 'GIT_UNAVAILABLE' }} · branch {{ gitCollaboration?.repository.integration_branch ?? 'UNKNOWN' }} · HEAD {{ gitCollaboration?.repository.head_commit ?? 'UNKNOWN' }}</p>
          <h3>Agent workspaces</h3>
          <p v-if="!gitCollaboration?.workspaces.length">尚无已分配 Agent worktree。</p>
          <article v-for="item in gitCollaboration?.workspaces ?? []" :key="item.workspace_id">
            <p>{{ item.agent_id }} · task {{ item.task_id }} · {{ item.status }}</p>
            <p>branch {{ item.branch_ref }} · base {{ item.base_commit }} · head {{ item.current_head }}</p>
            <p>scope {{ item.allowed_scope.join(', ') }} · changed {{ item.submitted_result?.changed_files.join(', ') || 'UNCOMMITTED' }} · {{ item.submitted_result?.scope_status ?? 'NOT_SUBMITTED' }}</p>
          </article>
          <h3>Merge queue</h3>
          <p v-if="!gitCollaboration?.integrations.length">尚无 integration candidate。</p>
          <article v-for="item in gitCollaboration?.integrations ?? []" :key="item.integration_id">
            <p>{{ item.integration_id }} · Git {{ item.git_status }} · {{ item.branch_status }}</p>
            <p>target {{ item.target_head }} · integration head {{ item.integration_head }}</p>
            <p>eligibility {{ item.latest_projection?.merge_eligibility ?? 'CHECKS_NOT_RUN' }} · evidence {{ item.latest_projection?.evidence_freshness ?? 'UNAVAILABLE' }}</p>
            <p>diagnostics {{ item.latest_projection?.diagnostics.join(', ') || item.conflicted_files.join(', ') || 'none' }}</p>
            <p>Semantic {{ item.latest_projection?.semantic?.candidate_canonical_identity ?? 'UNAVAILABLE' }} · unresolved {{ item.latest_projection?.semantic?.unresolved_count ?? 'UNKNOWN' }}</p>
            <p>DRC/LVS {{ item.latest_projection?.design?.drc.result.acceptable === true ? 'PASS' : item.latest_projection?.design ? 'FAIL' : 'UNAVAILABLE' }} / {{ item.latest_projection?.design?.lvs.result.overall_status ?? 'UNAVAILABLE' }}</p>
            <p>Build/Test {{ item.latest_projection?.execution?.build.result ?? item.latest_projection?.checks?.BUILD ?? 'UNAVAILABLE' }} / {{ item.latest_projection?.execution?.test.result ?? item.latest_projection?.checks?.TEST ?? 'UNAVAILABLE' }}</p>
            <p>Post-merge observation {{ item.production_observation?.status ?? item.canonical_publication ?? 'NOT_PUBLISHED' }} · Canonical {{ item.production_observation?.production_current_after ?? 'UNCHANGED' }}</p>
          </article>
        </section>
        <section v-else-if="page === 'verify'" class="panel">
          <h2>验证与关闭</h2>
          <p>Tests {{ workspace.freshness.TESTS }} · Verification {{ workspace.freshness.VERIFICATION }} · Approval {{ workspace.freshness.APPROVAL }}</p>
          <p>Policy {{ workspace.eligibility.policy_version }} · change v{{ workspace.eligibility.change_version }} · authorization {{ workspace.eligibility.authorization }}</p>
          <template v-if="ownerSession?.authenticated && ownerSession.principal">
            <p>Authenticated as Local Owner · {{ ownerSession.principal.principal_id }} · session expires {{ new Date(ownerSession.principal.expires_at * 1000).toLocaleString() }}</p>
            <button :disabled="ownerBusy || !workspace.eligibility.actions.approve?.allowed" @click="decideApproval('APPROVE')">Approve exact revision</button>
            <button :disabled="ownerBusy || !workspace.eligibility.actions.approve?.allowed" @click="decideApproval('REJECT')">Reject</button>
            <button :disabled="ownerBusy" @click="revokeOwner">Logout / revoke session</button>
          </template>
          <template v-else>
            <p>Owner authentication unavailable. Read the one-time token from the local daemon terminal; Agent claims and display names cannot approve.</p>
            <label>One-time Local Owner token <input v-model="ownerToken" type="password" autocomplete="off" /></label>
            <button :disabled="ownerBusy || !ownerToken" @click="authenticateOwner">Authenticate Local Owner</button>
          </template>
          <p v-if="ownerResult">{{ ownerResult }}</p>
          <p v-if="ownerError" role="alert">{{ ownerError }}</p>
          <p v-for="(action, name) in workspace.eligibility.actions" :key="name">
            {{ name }}: {{ action.allowed ? 'ALLOWED' : 'DENIED' }}
            <span v-if="!action.allowed">· {{ action.denial_reasons.join(', ') }}</span>
          </p>
          <p>批准由服务端会话和精确设计版本裁决；本页不自行生成身份或收据。</p>
        </section>
        <section v-else class="panel">
          <h2>Agent 报告 → Rintel 观察</h2>
          <p>Re-index {{ workspace.activity.reindex.status }} · adoption {{ workspace.activity.reindex.adoption_status }} · run {{ workspace.activity.reindex.run_identity ?? 'UNAVAILABLE' }} · canonical {{ workspace.activity.reindex.produced_canonical_revision ?? 'previous observation' }}</p>
          <p>Agent 自述不会生成 ACTUAL；只有索引 receipt 能绑定新 revision。</p>
          <p v-for="receipt in workspace.activity.receipts" :key="receipt.id">
            {{ receipt.command }} · {{ receipt.id }} · {{ receipt.actor }} · {{ receipt.state_after }}
          </p>
        </section>
      </template>
    </main>
    <aside v-if="selectedEvidence" class="change-inspector" aria-label="Evidence Inspector">
      <button @click="selectEvidence(null)">关闭</button>
      <h2>Evidence Inspector</h2>
      <p>{{ selectedEvidence.entity_id }} · {{ selectedEvidence.canonical_revision }}</p>
      <p>support {{ selectedEvidence.support_status }}</p>
      <p>authority {{ selectedEvidence.authority }} · truth {{ selectedEvidence.truth_class }} · resolution {{ selectedEvidence.target_resolution }} · coverage {{ selectedEvidence.coverage }} · modality {{ selectedEvidence.execution_modality }}</p>
      <p>provider {{ selectedEvidence.provider.join(', ') || 'UNKNOWN' }}</p>
      <h3>Admission support receipts</h3>
      <p v-if="!selectedEvidence.support_receipts.length">LEGACY_UNKNOWN · no admitted support receipt</p>
      <div v-for="receipt in selectedEvidence.support_receipts" :key="receipt.support_receipt_id">
        <p>{{ receipt.support_receipt_id }} · {{ receipt.lane_id }} · {{ receipt.producer }}@{{ receipt.producer_version }}</p>
        <p>admission {{ receipt.admission_id }} · evidence {{ receipt.evidence_id }}</p>
        <p>truth {{ receipt.truth_class }} · resolution {{ receipt.target_resolution }} · coverage {{ receipt.coverage }} · modality {{ receipt.execution_modality }}</p>
        <p>reuse {{ receipt.reuse_source_receipt_id ?? 'NONE' }} · provenance {{ JSON.stringify(receipt.provenance) }}</p>
        <p v-if="receipt.limitations.length">{{ receipt.limitations.join('；') }}</p>
      </div>
      <p>SourceSpan refs {{ selectedEvidence.supporting_evidence_refs.join(', ') || 'UNAVAILABLE' }}</p>
      <p>{{ selectedEvidence.limitations.join('；') }}</p>
      <button @click="openSource(selectedEvidence)">查看已绑定 SourceSpan</button>
      <p v-if="sourceError" role="alert">{{ sourceError }}</p>
      <pre v-if="sourceContent">{{ sourceContent }}</pre>
    </aside>
  </div>
</template>

<style scoped>
.change-shell { display: grid; grid-template-columns: 220px minmax(0, 1fr) auto; min-height: 100vh; background: var(--bg); color: var(--text); }
.change-nav { padding: 22px 16px; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }
.change-nav button, .modes button { text-align: left; border: 0; background: transparent; color: var(--text); padding: 10px; cursor: pointer; }
.change-nav button[aria-current="page"], .modes button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
.change-main { padding: 28px; min-width: 0; }
.label { color: var(--text-muted); font: 11px var(--mono); letter-spacing: .08em; }
.identity-bar { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0; }
.identity-bar span { border: 1px solid var(--border); border-radius: 5px; padding: 6px; font: 11px var(--mono); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.cards article, .panel, .change-inspector { background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 18px; }
.panel { max-width: 1000px; }
.panel p { border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.change-inspector { width: 330px; overflow-wrap: anywhere; }
.change-inspector pre { white-space: pre-wrap; }
button { cursor: pointer; }
@media(max-width: 900px) { .change-shell { grid-template-columns: 1fr; }.change-nav { flex-direction: row; overflow-x: auto; }.change-inspector { width: auto; } }
</style>
