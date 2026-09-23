// TOPO-EDITOR-UX0: design editor controller.
// Owns IDE-style context menus / agent proposals / annotation editor state.
// Reads the flow store; every write still goes through flow store actions
// (which record design transactions).  Pure UI orchestration — no backend
// calls outside flowApi via the flow store, no canonical writes anywhere.
import { defineStore } from 'pinia'
import { useFlowStore } from './flow'
import type { AgentProposal, FlowBlock } from '../domain/flow'
import {
  suggestName, suggestAnnotation, suggestCacheInsert, sketchToProposal,
  explainTopology,
} from '../lib/designAgent'

export interface CtxMenuItem {
  id: string
  label: string
  hint?: string
  danger?: boolean
  disabled?: boolean
  separator?: boolean
  testid?: string
  action?: () => void
  children?: CtxMenuItem[]
}

export const useDesignStore = defineStore('design', {
  state: () => ({
    ctx: null as { x: number; y: number; items: CtxMenuItem[] } | null,
    proposal: null as AgentProposal | null,
    annotationBlockId: '' as string,
    annotationDraft: {} as Record<string, string>,
    showNewTopology: false,
    sketchPrompt: '' as string,
    showSketch: false,
    connectingPortId: '' as string,
  }),
  actions: {
    closeCtx(): void { this.ctx = null },
    openCtx(x: number, y: number, items: CtxMenuItem[]): void {
      this.ctx = { x, y, items }
    },
    openProposal(p: AgentProposal): void { this.proposal = p },
    closeProposal(): void { this.proposal = null },

    editAnnotation(blockId: string): void {
      const flow = useFlowStore()
      this.annotationBlockId = blockId
      const ann = flow.annotationOf(blockId) as Record<string, unknown>
      const tagList = Array.isArray(ann.tags) ? (ann.tags as string[]) : []
      this.annotationDraft = {
        displayName: String(ann.displayName ?? ''),
        description: String(ann.description ?? ''),
        responsibility: String(ann.responsibility ?? ''),
        notes: String(ann.notes ?? ''),
        tags: tagList.join(', '),
      }
    },
    closeAnnotation(): void {
      this.annotationBlockId = ''
      this.annotationDraft = {}
    },
    async saveAnnotation(): Promise<void> {
      const flow = useFlowStore()
      if (!this.annotationBlockId) return
      const { displayName, description, responsibility, notes, tags } = this.annotationDraft
      await flow.saveBlockAnnotation(this.annotationBlockId, {
        displayName: displayName.trim() || undefined,
        description: description.trim() || undefined,
        responsibility: responsibility.trim() || undefined,
        notes: notes.trim() || undefined,
        tags: tags.split(',').map((s) => s.trim()).filter(Boolean),
      })
      this.closeAnnotation()
    },

    // -- agent intents (all produce proposals; writes only on accept) ----
    agentName(blockIds: string[]): void {
      const flow = useFlowStore()
      if (!blockIds.length) return
      const blocks = blockIds
        .map((id) => flow.blocksById[id])
        .filter((b): b is FlowBlock => !!b)
      const names = blocks.map((b) => b.name)
      const ports = blocks.flatMap((b) => flow.ports.filter((p) => p.blockId === b.id))
      const s = suggestName(names, { ports, kind: blocks[0]?.kind })
      if (!s) return
      this.openProposal({
        agentActionId: `ag-${Date.now()}-name`,
        intent: 'naming',
        prompt: `命名 ${names.join('、')}`,
        summary: `为「${names.join('、')}」生成命名建议`,
        suggestions: [
          { primary: s.primary, alternates: s.alternates, reason: s.reason },
        ],
        affectedDesignIds: blockIds,
      })
    },

    agentDescribe(blockId: string): void {
      const flow = useFlowStore()
      const b = flow.blocksById[blockId]
      if (!b) return
      const ctx = {
        ports: flow.ports.filter((p) => p.blockId === blockId),
        nets: flow.nets.filter(
          (n) => n.sourceBlockId === blockId || n.targetBlockId === blockId),
        blocks: flow.blocks,
      }
      const { annotation, summary } = suggestAnnotation(b, ctx)
      this.openProposal({
        agentActionId: `ag-${Date.now()}-desc`,
        intent: 'annotation',
        prompt: `为 ${b.name} 生成设计说明`,
        summary,
        annotation,
        affectedDesignIds: [blockId],
      })
    },

    agentCachePatch(netId: string): void {
      const flow = useFlowStore()
      const net = flow.nets.find((n) => n.id === netId)
      if (!net) return
      const proposal = suggestCacheInsert(net, {
        blocks: flow.blocks,
        ports: flow.ports,
        nets: flow.nets,
      })
      if (proposal) this.openProposal(proposal)
    },

    agentSketch(): void {
      const flow = useFlowStore()
      const p = sketchToProposal(this.sketchPrompt)
      if (p) {
        p.affectedDesignIds = flow.visibleBlocks.map((b) => b.id)
        this.openProposal(p)
      }
    },

    explainTopology(): void {
      const flow = useFlowStore()
      const text = explainTopology({
        blocks: flow.visibleBlocks,
        ports: flow.ports,
        nets: flow.visibleNets,
      })
      this.openProposal({
        agentActionId: `ag-${Date.now()}-explain`,
        intent: 'explain',
        summary: '当前拓扑说明',
        explanation: text,
        affectedDesignIds: [],
      })
    },

    /** Sketch prompt from the canvas Agent menu (§21). */
    openSketch(): void {
      this.showSketch = true
      this.sketchPrompt = ''
    },
  },
})
