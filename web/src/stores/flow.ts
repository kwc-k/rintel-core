// Flow store: Software Circuit state (SPEC-P2 §3/§5/§18).  Every mutation
// goes through the backend; the canvas is a projection of this store.
// TOPO-EDITOR-UX0 §11/§22: every design mutation records a transaction
// (before/after) on a design-history stack → undo/redo; agent actions get
// provenance persisted on the design model via the API.
import { defineStore } from 'pinia'
import * as flowApi from '../api/flow'
import type { FlowMutationGuard } from '../api/design-lifecycle'
import { isApiError } from '../api/client'
import type {
  DesignTransaction, FlowBlock, FlowDto, FlowLayoutPos, FlowNet,
  FlowPort, FlowBlockKind, FlowBlockState, SemanticKind, ValidateReport,
  WritebackPreview, WritebackResult, AgentActionRecord,
} from '../domain/flow'

interface BlockDraft {
  code: string
}

interface ToastFn {
  (message: string, kind?: 'info' | 'error' | 'success'): void
}

let toastFn: ToastFn | null = null
export function registerFlowToast(fn: ToastFn): void {
  toastFn = fn
}

function toast(message: string, kind: 'info' | 'error' | 'success' = 'info') {
  try { toastFn?.(message, kind) } catch { /* noop */ }
}

let layoutTimer: number | undefined

/** Design-plane entities snapshot for one undo transaction. */
interface DesignSnapshot {
  blocks: Array<Pick<FlowBlock, 'id' | 'name' | 'kind' | 'state' | 'parentBlockId' | 'metaJson'>>
  ports: Array<Pick<FlowPort, 'id' | 'blockId' | 'name' | 'direction' | 'semanticKind' | 'codeType' | 'positionOrder'>>
  nets: Array<Pick<FlowNet, 'id' | 'sourcePortId' | 'targetPortId' | 'kind' | 'label' | 'metaJson'>>
}

export const useFlowStore = defineStore('flow', {
  state: () => ({
    flowId: '' as string,
    dto: null as FlowDto | null,
    loading: false,
    error: '' as string,
    selectedBlockId: '' as string,
    selectedPortId: '' as string,
    selectedNetId: '' as string,
    currentParentBlockId: '' as string, // '' = root scope
    validation: null as ValidateReport | null,
    validating: false,
    preview: null as WritebackPreview | null,
    applying: false,
    busy: false,
    drafts: {} as Record<string, BlockDraft>,
    positions: {} as Record<string, FlowLayoutPos>,
    // TOPO-EDITOR-UX0 §11: design transaction history (undo/redo)
    undoStack: [] as DesignTransaction[],
    redoStack: [] as DesignTransaction[],
    // §22: agent provenance (this session; persisted server-side too)
    agentActions: [] as AgentActionRecord[],
  }),

  getters: {
    flow(state) {
      return state.dto?.flow ?? null
    },
    blocks(state): FlowBlock[] {
      return state.dto?.blocks ?? []
    },
    ports(state): FlowPort[] {
      return state.dto?.ports ?? []
    },
    nets(state): FlowNet[] {
      return state.dto?.nets ?? []
    },
    blocksById(state): Record<string, FlowBlock> {
      const out: Record<string, FlowBlock> = {}
      for (const b of state.dto?.blocks ?? []) out[b.id] = b
      return out
    },
    portsById(state): Record<string, FlowPort> {
      const out: Record<string, FlowPort> = {}
      for (const p of state.dto?.ports ?? []) out[p.id] = p
      return out
    },
    /** Blocks rendered in the current scope (no parent). */
    visibleBlocks(state): FlowBlock[] {
      const scope = state.currentParentBlockId
      return (state.dto?.blocks ?? []).filter(
        (b) => (b.parentBlockId ?? '') === scope)
    },
    /** Nets whose both endpoints belong to the current scope blocks. */
    visibleNets(state): FlowNet[] {
      const ids = new Set(this.visibleBlocks.map((b) => b.id))
      return (state.dto?.nets ?? []).filter(
        (n) => ids.has(n.sourceBlockId ?? '') && ids.has(n.targetBlockId ?? ''))
    },
    selectedBlock(state): FlowBlock | null {
      if (!state.selectedBlockId) return null
      return this.blocksById[state.selectedBlockId] ?? null
    },
    selectedNet(state): FlowNet | null {
      return (state.dto?.nets ?? []).find((n) => n.id === state.selectedNetId) ?? null
    },
    breadcrumb(state): Array<{ id: string; name: string }> {
      const chain: Array<{ id: string; name: string }> = []
      let cur = state.currentParentBlockId
      let guard = 0
      while (cur && guard < 32) {
        const b = this.blocksById[cur]
        if (!b) break
        chain.unshift({ id: b.id, name: b.name })
        cur = b.parentBlockId ?? ''
        guard += 1
      }
      return chain
    },
    statusText(state): string {
      return state.validation?.status ?? '—'
    },
    // TOPO-EDITOR-UX0 §23: design-dirty when undos exist (edited state)
    designModified(state): boolean {
      return state.undoStack.length > 0
    },
    canUndo(state): boolean {
      return state.undoStack.length > 0
    },
    canRedo(state): boolean {
      return state.redoStack.length > 0
    },
    annotationOf(state) {
      return (blockId: string) => {
        const b = state.dto?.blocks.find((x) => x.id === blockId)
        if (!b) return {}
        try {
          return JSON.parse(b.metaJson || '{}')?.design ?? {}
        } catch {
          return {}
        }
      }
    },
  },

  actions: {
    mutationGuard(): FlowMutationGuard {
      return { flowId: this.flowId, revision: this.dto?.eda?.revision ?? '', commits: 0 }
    },

    async loadFlow(flowId: string): Promise<void> {
      const previousRevision = this.flowId === flowId ? this.dto?.eda?.revision : undefined
      if (this.flowId !== flowId) {
        this.undoStack = []
        this.redoStack = []
      }
      this.flowId = flowId
      this.loading = true
      this.error = ''
      try {
        this.dto = await flowApi.getFlow(flowId)
        if (previousRevision && this.dto.eda?.revision !== previousRevision) {
          this.undoStack = []
          this.redoStack = []
        }
        this.positions = { ...(this.dto.layout ?? {}) }
      } catch (err) {
        this.error = isApiError(err) ? err.message : String(err)
        toast(`加载 Flow 失败: ${this.error}`, 'error')
      } finally {
        this.loading = false
      }
    },

    async createFromSymbol(input: {
      repoId: string
      symbol: string
      snapshotId?: string | null
      name?: string | null
    }): Promise<string> {
      const dto = await flowApi.fromSymbol(input)
      this.dto = dto
      this.flowId = dto.flow.id
      this.positions = { ...(dto.layout ?? {}) }
      this.currentParentBlockId = ''
      this.undoStack = []
      this.redoStack = []
      return dto.flow.id
    },

    // TOPO-EDITOR-UX0 §1: blank software circuit (empty SoftwareNetlist).
    async createBlank(input: {
      repoId: string
      name?: string | null
    }): Promise<string> {
      const dto = await flowApi.blankFlow(input)
      this.dto = dto
      this.flowId = dto.flow.id
      this.positions = { ...(dto.layout ?? {}) }
      this.currentParentBlockId = ''
      this.undoStack = []
      this.redoStack = []
      return dto.flow.id
    },

    async createFromComponent(input: {
      workspaceId: string
      modelId: string
      componentId: string
      name?: string | null
    }): Promise<string> {
      const dto = await flowApi.fromComponent(input)
      this.dto = dto
      this.flowId = dto.flow.id
      this.undoStack = []
      this.redoStack = []
      return dto.flow.id
    },

    async refresh(): Promise<void> {
      if (!this.flowId) return
      const dto = await flowApi.getFlow(this.flowId)
      this.dto = dto
      this.positions = { ...(dto.layout ?? {}) }
    },

    /** Observe external harness edits without replacing an in-progress canvas draft. */
    async refreshIfRevisionChanged(): Promise<boolean> {
      const flowId = this.flowId
      if (!flowId || !this.dto) return false
      const observedRevision = this.dto.eda?.revision
      const dto = await flowApi.getFlow(flowId)
      if (this.flowId !== flowId || this.dto?.eda?.revision !== observedRevision ||
          dto.eda?.revision === observedRevision) return false
      this.dto = dto
      this.positions = { ...(dto.layout ?? {}) }
      // Local history was recorded against the previous DesignRevision.
      this.undoStack = []
      this.redoStack = []
      if (this.selectedBlockId && !dto.blocks.some((b) => b.id === this.selectedBlockId)) {
        this.selectedBlockId = ''
        this.selectedPortId = ''
      }
      if (this.selectedPortId && !dto.ports.some((p) => p.id === this.selectedPortId)) {
        this.selectedPortId = ''
      }
      if (this.selectedNetId && !dto.nets.some((n) => n.id === this.selectedNetId)) {
        this.selectedNetId = ''
      }
      if (this.currentParentBlockId && !dto.blocks.some((b) => b.id === this.currentParentBlockId)) {
        this.currentParentBlockId = ''
      }
      return true
    },

    selectBlock(id: string): void {
      this.selectedBlockId = id
      this.selectedNetId = ''
      this.selectedPortId = ''
    },

    selectPort(blockId: string, portId: string): void {
      this.selectedBlockId = blockId
      this.selectedNetId = ''
      this.selectedPortId = portId
    },
    selectNet(id: string): void {
      this.selectedNetId = id
      this.selectedBlockId = ''
    },
    clearSelection(): void {
      this.selectedBlockId = ''
      this.selectedNetId = ''
    },

    enterComposite(blockId: string, name: string): void {
      const b = this.blocksById[blockId]
      if (b?.kind !== 'composite') return
      this.currentParentBlockId = blockId
      this.clearSelection()
      toast(`进入 ${name}`)
    },
    exitComposite(): void {
      const b = this.blocksById[this.currentParentBlockId]
      this.currentParentBlockId = b?.parentBlockId ?? ''
      this.clearSelection()
    },

    // -- block operations -------------------------------------------------
    async addBlock(kind: FlowBlockKind, name: string): Promise<FlowBlock | null> {
      const state: FlowBlockState = kind === 'function' || kind === 'object'
        ? 'proposed' : 'existing'
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        const b = await flowApi.createBlock(this.flowId, {
          kind, name, state,
          parentBlockId: this.currentParentBlockId || null,
        }, guard)
        await this.refresh()
        this.recordTransaction(`add ${b.name}`, before, guard)
        this.selectBlock(b.id)
        return b
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        return null
      }
    },

    async updateBlock(blockId: string, body: {
      name?: string; code?: string; state?: string; meta?: Record<string, unknown> | null
    }): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.patchBlock(this.flowId, blockId, body, guard)
        await this.refresh()
        this.recordTransaction('update block', before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    async deleteBlocks(ids: string[]): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        for (const id of ids) await flowApi.deleteBlock(this.flowId, id, guard)
        await this.refresh()
        this.recordTransaction(`delete ${ids.length} block(s)`, before, guard)
        this.clearSelection()
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    async reparentBlock(blockId: string, parentBlockId: string | null): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.patchBlock(this.flowId, blockId, {
          parentBlockId: parentBlockId || null,
          clearParent: parentBlockId === null,
        }, guard)
        await this.refresh()
        this.recordTransaction('reparent', before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        await this.refresh()
      }
    },

    // -- port operations --------------------------------------------------
    async addPort(port: {
      blockId: string; name: string; direction: 'input' | 'output'
      semanticKind: SemanticKind; codeType?: string | null
    }): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.createPort(this.flowId, {
          blockId: port.blockId, name: port.name, direction: port.direction,
          semanticKind: port.semanticKind, codeType: port.codeType ?? null,
          positionOrder: 0,
        }, guard)
        await this.refresh()
        this.recordTransaction(`add port ${port.name}`, before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    async updatePort(portId: string, body: {
      name?: string; semanticKind?: string; codeType?: string | null
    }): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.patchPort(this.flowId, portId, body, guard)
        await this.refresh()
        this.recordTransaction('update port', before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    async deletePort(portId: string): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.deletePort(this.flowId, portId, guard)
        await this.refresh()
        this.recordTransaction('delete port', before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    // -- net operations ---------------------------------------------------
    async connectPorts(sourcePortId: string, targetPortId: string): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.createNet(this.flowId, { sourcePortId, targetPortId }, guard)
        await this.refresh()
        this.recordTransaction('connect', before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        await this.refresh()
      }
    },

    async deleteNets(ids: string[]): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        for (const id of ids) await flowApi.deleteNet(this.flowId, id, guard)
        await this.refresh()
        this.recordTransaction(`delete ${ids.length} net(s)`, before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    // -- composite ---------------------------------------------------------
    async createComposite(name: string, blockIds: string[]): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await flowApi.createComposite(this.flowId, name, blockIds, guard)
        await this.refresh()
        this.recordTransaction(`composite ${name}`, before, guard)
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    async expandNeighbors(): Promise<void> {
      try {
        await flowApi.expandFlow(this.flowId)
        await this.refresh()
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    // -- layout -----------------------------------------------------------
    applyLayoutPositions(moved: Record<string, FlowLayoutPos>): void {
      this.positions = { ...this.positions, ...moved }
      this.saveLayoutDebounced()
    },
    saveLayoutDebounced(): void {
      if (layoutTimer) window.clearTimeout(layoutTimer)
      layoutTimer = window.setTimeout(() => { void this.saveLayoutNow() }, 900)
    },
    async saveLayoutNow(): Promise<void> {
      try {
        await flowApi.patchFlow(this.flowId, { layout: { ...this.positions } })
      } catch {
        /* layout save is best-effort */
      }
    },

    // -- validation / writeback --------------------------------------------
    async validateFlow(): Promise<ValidateReport | null> {
      this.validating = true
      try {
        const v = await flowApi.validateFlow(this.flowId)
        this.validation = v
        return v
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        return null
      } finally {
        this.validating = false
      }
    },

    draftFor(blockId: string, fallback = ''): string {
      return this.drafts[blockId]?.code ?? fallback
    },
    setDraft(blockId: string, code: string): void {
      this.drafts[blockId] = { code }
    },

    async previewWriteback(blockId: string): Promise<WritebackPreview | null> {
      try {
        const block = this.blocksById[blockId]
        const code = this.draftFor(blockId, block?.code ?? '')
        const p = await flowApi.writebackPreview(this.flowId, blockId, code)
        this.preview = p
        return p
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        return null
      }
    },

    async applyWriteback(blockId: string, code?: string): Promise<WritebackResult | null> {
      this.applying = true
      try {
        const r = await flowApi.writebackApply(this.flowId, blockId, code ?? null)
        await this.refresh()
        return r
      } catch (err) {
        toast(isApiError(err) ? err.message : String(err), 'error')
        return null
      } finally {
        this.applying = false
      }
    },

    reset(): void {
      this.flowId = ''
      this.dto = null
      this.error = ''
      this.selectedBlockId = ''
      this.selectedNetId = ''
      this.currentParentBlockId = ''
      this.validation = null
      this.preview = null
      this.drafts = {}
      this.positions = {}
      this.undoStack = []
      this.redoStack = []
      this.agentActions = []
    },

    // -- TOPO-EDITOR-UX0: design snapshots + transaction log -------------
    snapshotDesign(): DesignSnapshot {
      const dto = this.dto
      return {
        blocks: (dto?.blocks ?? []).map((b) => ({
          id: b.id, name: b.name, kind: b.kind, state: b.state,
          parentBlockId: b.parentBlockId ?? null, metaJson: b.metaJson,
        })),
        ports: (dto?.ports ?? []).map((p) => ({
          id: p.id, blockId: p.blockId, name: p.name, direction: p.direction,
          semanticKind: p.semanticKind, codeType: p.codeType ?? null,
          positionOrder: p.positionOrder,
        })),
        nets: (dto?.nets ?? []).map((n) => ({
          id: n.id, sourcePortId: n.sourcePortId, targetPortId: n.targetPortId,
          kind: n.kind, label: n.label ?? null, metaJson: n.metaJson,
        })),
      }
    },

    /** Record a design transaction around the last mutation.  Call with
     * the snapshot captured BEFORE the mutation; after is captured from
     * the current dto (already refreshed). */
    recordTransaction(label: string, before: DesignSnapshot, guard: FlowMutationGuard): void {
      if (!guard.commits || !guard.revision || this.flowId !== guard.flowId ||
          this.dto?.eda?.revision !== guard.revision) {
        this.undoStack = []
        this.redoStack = []
        toast('Design changed during this edit; Undo history was cleared', 'info')
        return
      }
      const after = this.snapshotDesign()
      this.undoStack.push({
        label,
        before: JSON.stringify(before),
        after: JSON.stringify(after),
        revision: guard.revision,
        ts: Date.now(),
      })
      if (this.undoStack.length > 100) this.undoStack.shift()
      this.redoStack = []
    },

    /** Generic undo: re-apply `before` snapshot by diffing against current
     * and issuing inverse design ops through the API.  Deterministic for
     * the operations we support (add/remove/rename/port/net/annotation). */
    async undo(): Promise<void> {
      const txn = this.undoStack[this.undoStack.length - 1]
      if (!txn || !this.flowId) return
      const before = JSON.parse(txn.before) as DesignSnapshot
      const after = JSON.parse(txn.after) as DesignSnapshot
      const guard = { flowId: this.flowId, revision: txn.revision, commits: 0 }
      const ok = await this.restoreDesign(after, before, guard)
      if (!ok) return
      this.undoStack.pop()
      txn.revision = guard.revision
      if (this.undoStack.length) this.undoStack[this.undoStack.length - 1].revision = guard.revision
      this.redoStack.push(txn)
    },

    async redo(): Promise<void> {
      const txn = this.redoStack[this.redoStack.length - 1]
      if (!txn || !this.flowId) return
      const before = JSON.parse(txn.before) as DesignSnapshot
      const after = JSON.parse(txn.after) as DesignSnapshot
      const guard = { flowId: this.flowId, revision: txn.revision, commits: 0 }
      const ok = await this.restoreDesign(before, after, guard)
      if (!ok) return
      this.redoStack.pop()
      txn.revision = guard.revision
      if (this.redoStack.length) this.redoStack[this.redoStack.length - 1].revision = guard.revision
      this.undoStack.push(txn)
    },

    /** Diff `from` → `to` and apply inverse ops (design-plane only).
     * Order: blocks first (create missing), then ports, then nets. */
    async restoreDesign(from: DesignSnapshot, to: DesignSnapshot,
      guard: FlowMutationGuard): Promise<boolean> {
      const blocksTo = new Map(to.blocks.map((b) => [b.id, b]))
      const blocksFrom = new Map(from.blocks.map((b) => [b.id, b]))
      try {
        if (this.dto?.eda?.revision !== guard.revision) {
          throw new Error('Flow design changed before Undo/Redo')
        }
        // 1) delete blocks present in `from` but not `to`
        for (const b of from.blocks) {
          if (!blocksTo.has(b.id)) {
            await flowApi.deleteBlock(this.flowId, b.id, guard)
          }
        }
        // 2) create blocks missing in `from` (parents first, by snapshot order)
        for (const b of to.blocks) {
          if (!blocksFrom.has(b.id)) {
            await flowApi.createBlock(this.flowId, {
              kind: b.kind, name: b.name, state: b.state,
              parentBlockId: b.parentBlockId ?? null,
              meta: JSON.parse(b.metaJson || '{}'),
            }, guard)
          } else {
            const cur = blocksFrom.get(b.id)!
            if (cur.name !== b.name || cur.state !== b.state ||
                cur.kind !== b.kind || (cur.parentBlockId ?? null) !== (b.parentBlockId ?? null) ||
                cur.metaJson !== b.metaJson) {
              await flowApi.patchBlock(this.flowId, b.id, {
                name: b.name, state: b.state,
                parentBlockId: b.parentBlockId ?? null,
                  clearParent: b.parentBlockId == null,
                  meta: JSON.parse(b.metaJson || '{}'),
              }, guard)
            }
          }
        }
        await this.refresh()
        // 3) ports: delete extras, create missing
        const portsTo = new Map(to.ports.map((p) => [p.id, p]))
        const portsFrom = new Map(from.ports.map((p) => [p.id, p]))
        for (const p of from.ports) {
          if (!portsTo.has(p.id)) await flowApi.deletePort(this.flowId, p.id, guard)
        }
        for (const p of to.ports) {
          if (!portsFrom.has(p.id)) {
            await flowApi.createPort(this.flowId, {
              blockId: p.blockId, name: p.name, direction: p.direction,
              semanticKind: p.semanticKind, codeType: p.codeType,
              positionOrder: p.positionOrder,
            }, guard)
          }
        }
        await this.refresh()
        // 4) nets: delete extras, create missing (endpoints must exist)
        const netsTo = new Set(to.nets.map((n) => n.id))
        for (const n of from.nets) {
          if (!netsTo.has(n.id)) await flowApi.deleteNet(this.flowId, n.id, guard)
        }
        const netsFrom = new Set(from.nets.map((n) => n.id))
        for (const n of to.nets) {
          if (!netsFrom.has(n.id)) {
            await flowApi.createNet(this.flowId, {
              sourcePortId: n.sourcePortId,
              targetPortId: n.targetPortId,
              kind: n.kind,
              label: n.label,
            }, guard)
          }
        }
        await this.refresh()
        if (this.dto?.eda?.revision !== guard.revision) {
          throw new Error('Flow design changed during Undo/Redo')
        }
        return true
      } catch (err) {
        this.undoStack = []
        this.redoStack = []
        await this.refresh()
        toast(isApiError(err) ? err.message : String(err), 'error')
        return false
      }
    },

    /** Re-usable wrapper: capture before, run mutation, record transaction. */
    async withTransaction(label: string,
      run: (guard: FlowMutationGuard) => Promise<void>): Promise<void> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        await run(guard)
        this.recordTransaction(label, before, guard)
      } catch (err) {
        this.undoStack = []
        this.redoStack = []
        await this.refresh()
        toast(isApiError(err) ? err.message : String(err), 'error')
      }
    },

    /**
     * Apply a curated TopologyPatchOp set (agent §9/§21) with the standard
     * Preview → Accept flow enforced by the caller.  This method is the
     * ONLY write path for agent patches — it also records provenance.
     */
    async applyAgentPatch(
      proposal: AgentActionRecord,
      ops: Array<{
        op: string
        target?: string
        block?: { name: string; kind: FlowBlockKind; state: FlowBlockState; parentBlockId?: string | null }
        port?: { blockId: string; name: string; direction: 'input' | 'output'; semanticKind: SemanticKind }
        net?: { sourcePortId: string; targetPortId: string; kind: string; label?: string | null }
      }>,
    ): Promise<boolean> {
      const before = this.snapshotDesign()
      const guard = this.mutationGuard()
      try {
        let cacheBlockId = ''
        let cacheIn: string | null = null
        let cacheOut: string | null = null
        for (const op of ops) {
          if (op.op === 'addBlock' && op.block) {
            const b = await flowApi.createBlock(this.flowId, {
              kind: op.block.kind, name: op.block.name, state: op.block.state,
              parentBlockId: op.block.parentBlockId ?? null,
            }, guard)
            if (op.block.name === 'ResultCache') cacheBlockId = b.id
          } else if (op.op === 'removeBlock' && op.target) {
            await flowApi.deleteBlock(this.flowId, op.target, guard)
          } else if (op.op === 'addPort' && op.port) {
            const pid = op.port.blockId === '__cache__'
              ? cacheBlockId : op.port.blockId
            if (!pid) continue
            const p = await flowApi.createPort(this.flowId, {
              blockId: pid, name: op.port.name, direction: op.port.direction,
              semanticKind: op.port.semanticKind,
            }, guard)
            if (op.port.name === 'result') cacheIn = p.id
            if (op.port.name === 'cached_result') cacheOut = p.id
          } else if (op.op === 'removeNet' && op.target) {
            await flowApi.deleteNet(this.flowId, op.target, guard)
          } else if (op.op === 'addNet' && op.net) {
            const src = op.net.sourcePortId === '__cacheout__'
              ? (cacheOut ?? '') : op.net.sourcePortId
            const dst = op.net.targetPortId === '__cachein__'
              ? (cacheIn ?? '') : op.net.targetPortId
            if (!src || !dst) continue
            await flowApi.createNet(this.flowId, {
              sourcePortId: src, targetPortId: dst, kind: op.net.kind,
              label: op.net.label ?? null,
            }, guard)
          }
        }
        await this.persistAgentAction(proposal, guard)
        await this.refresh()
      } catch (err) {
        this.undoStack = []
        this.redoStack = []
        await this.refresh()
        toast(isApiError(err) ? err.message : String(err), 'error')
        return false
      }
      this.recordTransaction(`agent:${proposal.intent}`, before, guard)
      this.agentActions.push(proposal)
      return true
    },

    async persistAgentAction(action: AgentActionRecord,
      guard?: FlowMutationGuard): Promise<void> {
      if (!this.flowId) return
      try {
        await flowApi.recordAgentAction(this.flowId, action, guard)
      } catch (err) {
        if (guard) throw err
        /* best-effort provenance for callers without a guarded patch */
      }
    },

    /**
     * Apply a curated annotation to a design block (UX5 annotation).
     * Design Annotation only — never a canonical-episode write.
     */
    async saveBlockAnnotation(
      blockId: string, ann: Record<string, unknown>,
    ): Promise<void> {
      await this.withTransaction('annotation', async (guard) => {
        try {
          await flowApi.patchBlock(this.flowId, blockId, { meta: { design: ann } }, guard)
        } catch (err) {
          toast(isApiError(err) ? err.message : String(err), 'error')
          throw err
        }
        await this.refresh()
      })
    },

    async renameBlock(blockId: string, name: string): Promise<void> {
      await this.withTransaction('rename', async (guard) => {
        try {
          await flowApi.patchBlock(this.flowId, blockId, { name }, guard)
        } catch (err) {
          toast(isApiError(err) ? err.message : String(err), 'error')
          throw err
        }
        await this.refresh()
      })
    },

    async patchNetMeta(netId: string, body: {
      kind?: string; label?: string | null; clearLabel?: boolean
      meta?: Record<string, unknown> | null
    }): Promise<void> {
      await this.withTransaction('net-edit', async (guard) => {
        try {
          await flowApi.patchNet(this.flowId, netId, body, guard)
        } catch (err) {
          toast(isApiError(err) ? err.message : String(err), 'error')
          throw err
        }
        await this.refresh()
      })
    },
  },
})
