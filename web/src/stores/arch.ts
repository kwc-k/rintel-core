// Architecture store (S3-WEB-CONTRACT §4 + S4-WEB-CONTRACT §4). Semantic
// assets live only in memory + the server; they are recovered from the
// bootstrap on reload and never persisted to localStorage. Every semantic
// mutation is written to the API immediately (R7) with a Saving/Saved/Failed
// sync state; layout is debounced separately (800ms, best-effort).
//
// S4: all mutations are scoped to the ACTIVE model (`workspace.activeModelId`
// — as-is model, or the proposal being edited in TO-BE mode). With no active
// model set this degrades to the S3 behavior (the AS-IS model).

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { archApi } from '../api/arch'
import { ApiError, isApiError } from '../api/client'
import type {
  ArchBootstrap,
  ArchComponent,
  ArchDiff,
  ArchKind,
  ArchLayout,
  ArchMapping,
  ArchModel,
  ArchRelation,
  ImpactResult,
  ModelMode,
  RelationKind,
} from '../domain/architecture'
import { asIsModelId } from '../domain/architecture'
import { evidenceEntityOf } from '../domain/workspace'
import { useWorkspaceStore } from './workspace'
import { useRepoStore } from './repo'
import { useToastStore } from './toast'

export type LoadState = 'idle' | 'loading' | 'ready' | 'error'
export type SyncState = 'saved' | 'saving' | 'failed'

export interface ComponentBlockers {
  children: { id: string; name: string }[]
  relations: { id: string; kind: string; srcId: string; dstId: string }[]
}

export type DeleteComponentResult = { status: 'deleted' } | { status: 'blocked'; code: string; blockers: ComponentBlockers }

const LAYOUT_DEBOUNCE_MS = 800

export const useArchStore = defineStore('arch', () => {
  const bootstrap = ref<ArchBootstrap | null>(null)
  const loadState = ref<LoadState>('idle')
  const syncState = ref<SyncState>('saved')
  const diff = ref<ArchDiff | null>(null)
  const diffLoading = ref(false)
  const impact = ref<ImpactResult | null>(null)
  const impactLoading = ref(false)
  const impactError = ref(false)

  let layoutTimer: ReturnType<typeof setTimeout> | null = null
  let pendingLayout: ArchLayout | null = null

  const workspace = useWorkspaceStore()
  const repo = useRepoStore()
  const toast = useToastStore()

  // -- model helpers -----------------------------------------------------

  function modelById(id: string | null): ArchModel | null {
    if (!id || !bootstrap.value) return null
    return bootstrap.value.models.find((m) => m.id === id) ?? null
  }

  /** The model the canvas currently shows and mutations write to. */
  const activeModel = (): ArchModel | null => {
    const explicit = modelById(workspace.activeModelId)
    if (explicit) return explicit
    const asIs = bootstrap.value ? asIsModelId(bootstrap.value) : null
    return asIs ? modelById(asIs) : null
  }

  function requireContext(): { workspaceId: string; modelId: string } {
    const workspaceId = workspace.workspaceId
    const modelId = activeModel()?.id ?? null
    if (!workspaceId || !modelId) throw new Error('architecture workspace is not loaded')
    return { workspaceId, modelId }
  }

  /** Components / relations / layout of one model (bootstrap is flat). */
  function componentsOfModel(modelId: string): ArchComponent[] {
    return bootstrap.value?.components.filter((c) => c.modelId === modelId) ?? []
  }

  function relationsOfModel(modelId: string): ArchRelation[] {
    return bootstrap.value?.relations.filter((r) => r.modelId === modelId) ?? []
  }

  function layoutOfModel(modelId: string): ArchLayout {
    return bootstrap.value?.layouts?.[modelId] ?? {}
  }

  /** Reconcile the persisted mode/activeModelId against the fresh bootstrap.
   * Fallback chain: invalid model → AS-IS; to_be/diff need a proposal.
   * Direct assignments (not setActiveModelId): reconciliation happens during
   * bootstrap/hydration and must not wipe the focus we are about to restore. */
  function reconcileModel(): void {
    const b = bootstrap.value
    if (!b) return
    const asIs = asIsModelId(b)
    const activeId = workspace.activeModelId
    if (!activeId || !modelById(activeId)) {
      workspace.activeModelId = asIs
    }
    const model = modelById(workspace.activeModelId)
    if (model && model.kind === 'as_is' && workspace.mode !== 'as_is') {
      workspace.setMode('as_is')
    }
  }

  function repoLabel(): string {
    const found = repo.repos.find((r) => r.id === workspace.repoId)
    if (found?.rootPath) {
      const base = found.rootPath.replace(/\/+$/, '').split('/').pop()
      if (base) return base
    }
    return workspace.repoId ?? 'workspace'
  }

  function resolveArchFocus(): void {
    const f = workspace.focus
    if (f && f.plane === 'arch' && f.entityType === 'component') {
      const exists = bootstrap.value?.components.some((c) => c.id === f.id) ?? false
      if (!exists) workspace.setFocus(null)
    }
  }

  async function loadBootstrap(): Promise<void> {
    const wid = workspace.workspaceId
    if (!wid) return
    loadState.value = 'loading'
    try {
      bootstrap.value = await archApi.getBootstrap(wid, workspace.snapshotId ?? undefined)
      loadState.value = 'ready'
      reconcileModel()
      resolveArchFocus()
    } catch (err) {
      loadState.value = 'error'
      toast.reportError(err)
    }
  }

  /** Load an existing workspace; creation requires an explicit DesignChange. */
  async function ensureWorkspace(): Promise<void> {
    if (!workspace.repoId) return
    try {
      const list = await archApi.listWorkspaces()
      let wid = list.items.find((w) => w.repoId === workspace.repoId)?.id ?? null
      if (!wid) {
        workspace.setWorkspaceId(null)
        return
      }
      workspace.setWorkspaceId(wid)
      await loadBootstrap()
    } catch (err) {
      toast.reportError(err)
      throw err
    }
  }

  /** Wrap a semantic mutation in the Saving→Saved/Failed sync state machine. */
  async function withSync<T>(mutate: () => Promise<T>): Promise<T> {
    syncState.value = 'saving'
    let result: T
    try {
      result = await mutate()
    } catch (err) {
      syncState.value = 'failed'
      toast.reportError(err)
      throw err
    }
    syncState.value = 'saved'
    // Best-effort re-fetch; a failed refresh reports through loadBootstrap
    // and must not flip syncState (the write itself succeeded).
    diff.value = null // working model changed → cached diff is stale
    await loadBootstrap()
    return result
  }

  // -- S4: model lifecycle -------------------------------------------------

  /** Fork the AS-IS model into a new proposal (frozen baseline, empty diff). */
  async function forkProposal(name: string): Promise<ArchModel | null> {
    const wid = workspace.workspaceId
    const asIs = bootstrap.value ? asIsModelId(bootstrap.value) : null
    if (!wid || !asIs) return null
    syncState.value = 'saving'
    try {
      const res = await archApi.forkModel(wid, { kind: 'proposal', name })
      syncState.value = 'saved'
      diff.value = res.diff
      workspace.setActiveModelId(res.model.id)
      workspace.setMode('to_be')
      workspace.setCenterTab('architecture')
      toast.reportSuccess(`已创建 Proposal「${res.model.name}」`)
      // Optimistic activation (S5B finding): without the stub, the canvas
      // keeps resolving the AS-IS model until loadBootstrap() lands, so a
      // DIFF/TO-BE click (or even an add-component) right after the dialog
      // closes hit the "请先创建或选择 Proposal" guard — or worse, routed an
      // edit into AS-IS.  Insert the proposal immediately; the refresh below
      // replaces the bootstrap with authoritative state including copies.
      const b = bootstrap.value
      if (b && !b.models.some((m) => m.id === res.model.id)) {
        bootstrap.value = {
          ...b,
          models: [...b.models, res.model],
          layouts: { ...b.layouts, [res.model.id]: {} },
        }
      }
      await loadBootstrap()
      return res.model
    } catch (err) {
      syncState.value = 'failed'
      toast.reportError(err)
      throw err
    }
  }

  /** Refresh the frozen diff of the active proposal (DIFF mode entry). */
  async function loadDiff(force = false): Promise<ArchDiff | null> {
    const wid = workspace.workspaceId
    const model = activeModel()
    if (!wid || !model || model.kind !== 'proposal') return null
    if (!force && diff.value && diff.value.modelId === model.id) return diff.value
    diffLoading.value = true
    try {
      diff.value = await archApi.getModelDiff(wid, model.id, workspace.snapshotId ?? undefined)
      return diff.value
    } catch (err) {
      diff.value = null
      toast.reportError(err)
      return null
    } finally {
      diffLoading.value = false
    }
  }

  /** Structural Impact for the active proposal (subset or all changes). */
  async function loadImpact(changeIds?: string[]): Promise<ImpactResult | null> {
    const wid = workspace.workspaceId
    const model = activeModel()
    if (!wid || !model || model.kind !== 'proposal') return null
    impactLoading.value = true
    impactError.value = false
    try {
      impact.value = await archApi.postImpact(wid, model.id, {
        snapshotId: workspace.snapshotId ?? undefined,
        changeIds,
      })
      return impact.value
    } catch (err) {
      impactError.value = true
      toast.reportError(err)
      return null
    } finally {
      impactLoading.value = false
    }
  }

  // -- component / relation / mapping mutations (active-model scoped) ------

  /** Single component without evidence (S4 .add-comp-btn / TO-BE additions). */
  async function createComponent(name: string, kind: ArchKind, description?: string): Promise<ArchComponent | null> {
    const { workspaceId, modelId } = requireContext()
    try {
      const res = await withSync(() =>
        archApi.createComponent(workspaceId, { modelId, kind, name, description }),
      )
      workspace.setFocus({ plane: 'arch', entityType: 'component', id: res.id })
      toast.reportSuccess(`已添加组件「${res.name}」`)
      return res
    } catch (err) {
      if (isApiError(err) && (err.status === 422 || err.status === 409)) {
        toast.push(err.message, 'error')
      } else {
        toast.reportError(err)
      }
      throw err
    }
  }

  async function createComponentBatch(name: string, kind: ArchKind): Promise<ArchComponent | null> {
    const { workspaceId, modelId } = requireContext()
    const entityIds: string[] = []
    for (const ref of workspace.selected) {
      const e = evidenceEntityOf(ref)
      if (e) entityIds.push(e.entityId)
    }
    if (entityIds.length === 0) return null

    try {
      const res = await archApi.createComponentBatch(workspaceId, { modelId, kind, name, entityIds })
      workspace.clearSelection()
      workspace.setCenterTab('architecture')
      workspace.setFocus({ plane: 'arch', entityType: 'component', id: res.component.id })
      toast.reportSuccess(`已创建组件「${res.component.name}」`)
      diff.value = null
      await loadBootstrap()
      return res.component
    } catch (err) {
      // 422/409 keep the selection and surface the message inline (the dialog
      // stays open). Other errors are reported normally.
      if (isApiError(err) && (err.status === 422 || err.status === 409)) {
        toast.push(err.message, 'error')
      } else {
        toast.reportError(err)
      }
      throw err
    }
  }

  function createRelation(input: { srcId: string; dstId: string; kind: RelationKind; label?: string }): Promise<ArchRelation> {
    const { workspaceId, modelId } = requireContext()
    return withSync(() =>
      archApi.createRelation(workspaceId, {
        modelId,
        kind: input.kind,
        srcId: input.srcId,
        dstId: input.dstId,
        label: input.label,
      }),
    )
  }

  function updateRelation(relationId: string, patch: { kind?: RelationKind; label?: string }): Promise<ArchRelation> {
    const { workspaceId, modelId } = requireContext()
    return withSync(() => archApi.updateRelation(workspaceId, relationId, { modelId, kind: patch.kind, label: patch.label }))
  }

  function deleteRelation(relationId: string): Promise<{ ok: boolean }> {
    const { workspaceId, modelId } = requireContext()
    return withSync(() => archApi.deleteRelation(workspaceId, relationId, modelId))
  }

  function createMapping(componentId: string, entityType: 'node' | 'edge', entityId: string, note?: string): Promise<ArchMapping> {
    const { workspaceId } = requireContext()
    return withSync(() => archApi.createMapping(workspaceId, { componentId, entityType, entityId, note }))
  }

  /** Map the current evidence selection onto a component (.ms-map-to-component). */
  async function mapSelectionToComponent(componentId: string): Promise<ArchMapping[] | null> {
    const entityIds: string[] = []
    for (const ref of workspace.selected) {
      const e = evidenceEntityOf(ref)
      if (e) entityIds.push(e.entityId)
    }
    if (entityIds.length === 0) return null
    try {
      const res = await withSync(() =>
        archApi.createMappingsBatch(workspace.workspaceId ?? '', { componentId, entityIds }),
      )
      workspace.clearSelection()
      toast.reportSuccess(`已映射 ${res.mappings.length} 个实体到组件`)
      return res.mappings
    } catch (err) {
      if (isApiError(err) && (err.status === 422 || err.status === 409)) {
        toast.push(err.message, 'error')
      } else {
        toast.reportError(err)
      }
      throw err
    }
  }

  async function createMappings(componentId: string, entityIds: string[]): Promise<ArchMapping[]> {
    if (entityIds.length === 0) return []
    const { workspaceId } = requireContext()
    const res = await withSync(() => archApi.createMappingsBatch(workspaceId, { componentId, entityIds }))
    return res.mappings
  }

  function deleteMapping(mappingId: string): Promise<{ ok: boolean }> {
    const { workspaceId } = requireContext()
    return withSync(() => archApi.deleteMapping(workspaceId, mappingId))
  }

  function updateComponent(
    componentId: string,
    patch: { name?: string; description?: string; kind?: ArchKind; parentId?: string | null },
  ): Promise<ArchComponent> {
    const { workspaceId, modelId } = requireContext()
    return withSync(() =>
      archApi.updateComponent(workspaceId, componentId, {
        modelId,
        name: patch.name,
        description: patch.description,
        kind: patch.kind,
        parentId: patch.parentId,
      }),
    )
  }

  async function deleteComponent(componentId: string, opts: { subtree?: boolean } = {}): Promise<DeleteComponentResult> {
    const { workspaceId, modelId } = requireContext()
    syncState.value = 'saving'
    try {
      await archApi.deleteComponent(workspaceId, componentId, { modelId, subtree: opts.subtree })
    } catch (err) {
      if (
        !opts.subtree &&
        isApiError(err) &&
        err.status === 409 &&
        (err.code === 'component_has_children' || err.code === 'component_has_relations')
      ) {
        syncState.value = 'failed'
        return { status: 'blocked', code: err.code, blockers: extractBlockers(err) }
      }
      syncState.value = 'failed'
      toast.reportError(err)
      throw err
    }
    syncState.value = 'saved'
    diff.value = null
    await loadBootstrap()
    return { status: 'deleted' }
  }

  function extractBlockers(err: ApiError): ComponentBlockers {
    const d = (err.details ?? {}) as {
      children?: { id: string; name: string }[]
      relations?: { id: string; kind: string; src_id: string; dst_id: string }[]
    }
    return {
      children: (d.children ?? []).map((c) => ({ id: c.id, name: c.name })),
      relations: (d.relations ?? []).map((r) => ({ id: r.id, kind: r.kind, srcId: r.src_id, dstId: r.dst_id })),
    }
  }

  // -- layout (per active model; TO-BE positions never leak into AS-IS) ----

  async function flushLayout(): Promise<void> {
    const layout = pendingLayout
    pendingLayout = null
    if (!layout) return
    const wid = workspace.workspaceId
    const modelId = activeModel()?.id
    if (!wid || !modelId) return
    try {
      await archApi.putLayout(wid, modelId, layout)
    } catch (err) {
      toast.reportError(err)
    }
  }

  function saveLayoutDebounced(layout: ArchLayout): void {
    pendingLayout = { ...(pendingLayout ?? {}), ...layout }
    if (layoutTimer) clearTimeout(layoutTimer)
    layoutTimer = setTimeout(() => void flushLayout(), LAYOUT_DEBOUNCE_MS)
  }

  function componentById(id: string): ArchComponent | null {
    return bootstrap.value?.components.find((c) => c.id === id) ?? null
  }

  function relationById(id: string): ArchRelation | null {
    return bootstrap.value?.relations.find((r) => r.id === id) ?? null
  }

  function mappingsForComponent(componentId: string): ArchMapping[] {
    return bootstrap.value?.mappings.filter((m) => m.componentId === componentId) ?? []
  }

  return {
    bootstrap,
    loadState,
    syncState,
    diff,
    diffLoading,
    impact,
    impactLoading,
    impactError,
    modelById,
    activeModel,
    componentsOfModel,
    relationsOfModel,
    layoutOfModel,
    reconcileModel,
    ensureWorkspace,
    loadBootstrap,
    forkProposal,
    loadDiff,
    loadImpact,
    createComponent,
    createComponentBatch,
    createRelation,
    updateRelation,
    deleteRelation,
    createMapping,
    createMappings,
    mapSelectionToComponent,
    deleteMapping,
    updateComponent,
    deleteComponent,
    saveLayoutDebounced,
    componentById,
    relationById,
    mappingsForComponent,
  }
})
