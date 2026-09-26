import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useFlowStore } from '../../src/stores/flow'
import type { FlowDto } from '../../src/domain/flow'

vi.mock('../../src/api/flow', () => ({
  getFlow: vi.fn(),
  createBlock: vi.fn(),
  recordAgentAction: vi.fn(),
  patchBlock: vi.fn(),
  deleteBlock: vi.fn(),
}))
import * as flowApi from '../../src/api/flow'

function dto(revision: string, blockId = 'node-stable', name = 'Solver', withConnections = false) {
  return {
    flow: { id: 'flow-stable', name: 'Circuit' },
    eda: { revision },
    blocks: [{ id: blockId, name, kind: 'function', state: 'proposed', parentBlockId: null, metaJson: '{}', ports: [] }],
    ports: withConnections ? [
      { id: 'port-stable', blockId, name: 'out', direction: 'output', semanticKind: 'data', codeType: null, positionOrder: 0 },
    ] : [],
    nets: withConnections ? [
      { id: 'net-stable', sourcePortId: 'port-stable', targetPortId: 'port-stable', kind: 'data', label: null, metaJson: '{}' },
    ] : [],
    bindings: [], layout: {},
  }
}

function localRename(store: ReturnType<typeof useFlowStore>) {
  store.flowId = 'flow-stable'
  store.dto = dto('revision-1', 'node-stable', 'Original') as never
  const before = store.snapshotDesign()
  store.dto = dto('revision-2', 'node-stable', 'Local') as never
  store.recordTransaction('rename', before, { flowId: 'flow-stable', revision: 'revision-2', commits: 1 })
}

describe('external DesignRevision observation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('keeps stable selection on a changed revision and ignores unchanged reads', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = dto('revision-1') as never
    store.selectedBlockId = 'node-stable'
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-1') as never)
      .mockResolvedValueOnce(dto('revision-2') as never)
    expect(await store.refreshIfRevisionChanged()).toBe(false)
    expect((store.dto as FlowDto | null)?.eda?.revision).toBe('revision-1')
    expect(await store.refreshIfRevisionChanged()).toBe(true)
    expect((store.dto as FlowDto | null)?.eda?.revision).toBe('revision-2')
    expect(store.selectedBlockId).toBe('node-stable')
  })

  it('clears a removed selection rather than pointing at a different display ordinal', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = dto('revision-1') as never
    store.selectedBlockId = 'node-stable'
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-2', 'new-node') as never)
    expect(await store.refreshIfRevisionChanged()).toBe(true)
    expect(store.selectedBlockId).toBe('')
  })

  it('discards local Undo when Harness renames the same block', async () => {
    const store = useFlowStore()
    localRename(store)
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-3', 'node-stable', 'Harness') as never)

    expect(await store.refreshIfRevisionChanged()).toBe(true)
    expect(store.canUndo).toBe(false)
    await store.undo()
    expect(flowApi.patchBlock).not.toHaveBeenCalled()
    expect(store.dto?.blocks[0]?.name).toBe('Harness')
  })

  it('does not let stale Undo delete ports and nets added by Harness', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = { ...dto('revision-1'), blocks: [] } as never
    const before = store.snapshotDesign()
    store.dto = dto('revision-2') as never
    store.recordTransaction('add block', before, { flowId: 'flow-stable', revision: 'revision-2', commits: 1 })
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-3', 'node-stable', 'Solver', true) as never)

    expect(await store.refreshIfRevisionChanged()).toBe(true)
    await store.undo()
    expect(flowApi.deleteBlock).not.toHaveBeenCalled()
    expect((store.dto as FlowDto | null)?.ports.map((port) => port.id)).toEqual(['port-stable'])
    expect((store.dto as FlowDto | null)?.nets.map((net) => net.id)).toEqual(['net-stable'])
  })

  it('clears Redo after an external DesignRevision change', async () => {
    const store = useFlowStore()
    localRename(store)
    store.redoStack = [...store.undoStack]
    store.undoStack = []
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-3', 'node-stable', 'Harness') as never)

    expect(await store.refreshIfRevisionChanged()).toBe(true)
    expect(store.canRedo).toBe(false)
    await store.redo()
    expect(flowApi.patchBlock).not.toHaveBeenCalled()
  })

  it('keeps local Undo/Redo history on a no-op refresh', async () => {
    const store = useFlowStore()
    localRename(store)
    store.redoStack = [...store.undoStack]
    const undoBefore = [...store.undoStack]
    const redoBefore = [...store.redoStack]
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-2', 'node-stable', 'Local') as never)

    expect(await store.refreshIfRevisionChanged()).toBe(false)
    expect(store.undoStack).toEqual(undoBefore)
    expect(store.redoStack).toEqual(redoBefore)
  })

  it('discards a delayed poll response after a local revision advances', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = dto('revision-1', 'node-stable', 'Original') as never
    const before = store.snapshotDesign()
    let resolvePoll!: (value: FlowDto) => void
    vi.mocked(flowApi.getFlow).mockReturnValueOnce(new Promise((resolve) => { resolvePoll = resolve }) as never)

    const pending = store.refreshIfRevisionChanged()
    store.dto = dto('revision-2', 'node-stable', 'Local') as never
    store.recordTransaction('rename', before, { flowId: 'flow-stable', revision: 'revision-2', commits: 1 })
    resolvePoll(dto('revision-1', 'node-stable', 'Original') as never)

    expect(await pending).toBe(false)
    expect((store.dto as FlowDto | null)?.eda?.revision).toBe('revision-2')
    expect(store.canUndo).toBe(true)
  })

  it('does not record Harness changes as local Undo when they arrive before mutation readback', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = { ...dto('revision-1'), blocks: [] } as never
    vi.mocked(flowApi.createBlock).mockImplementation(async (_flowId, _input, guard) => {
      if (guard) {
        guard.revision = 'revision-2'
        guard.commits += 1
      }
      return { id: 'node-stable', name: 'Local' } as never
    })
    const external = dto('revision-3', 'node-stable', 'Local')
    external.blocks.push({ ...external.blocks[0], id: 'harness-node', name: 'Harness' })
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(external as never)

    await store.addBlock('function', 'Local')
    expect(store.canUndo).toBe(false)
    await store.undo()
    expect(flowApi.deleteBlock).not.toHaveBeenCalled()
    expect((store.dto as FlowDto | null)?.blocks.map((block) => block.id)).toContain('harness-node')
  })

  it('preserves pure-local Undo and Redo', async () => {
    const store = useFlowStore()
    localRename(store)
    let currentName = 'Local'
    let currentRevision = 'revision-2'
    vi.mocked(flowApi.patchBlock).mockImplementation(async (_flowId, _blockId, body, guard) => {
      currentName = body.name ?? currentName
      currentRevision = currentRevision === 'revision-2' ? 'revision-3' : 'revision-4'
      if (guard) {
        guard.revision = currentRevision
        guard.commits += 1
      }
      return {} as never
    })
    vi.mocked(flowApi.getFlow).mockImplementation(async () => dto(currentRevision, 'node-stable', currentName) as never)

    await store.undo()
    expect(currentName).toBe('Original')
    expect(store.canRedo).toBe(true)
    await store.redo()
    expect(currentName).toBe('Local')
    expect(store.canUndo).toBe(true)
  })

  it('keeps two local rename transactions bound through successive Undo and Redo', async () => {
    const store = useFlowStore()
    localRename(store)
    const secondBefore = store.snapshotDesign()
    store.dto = dto('revision-3', 'node-stable', 'Second') as never
    store.recordTransaction('rename again', secondBefore,
      { flowId: 'flow-stable', revision: 'revision-3', commits: 1 })
    let currentName = 'Second'
    let revisionNumber = 3
    vi.mocked(flowApi.patchBlock).mockImplementation(async (_flowId, _blockId, body, guard) => {
      currentName = body.name ?? currentName
      revisionNumber += 1
      if (guard) { guard.revision = `revision-${revisionNumber}`; guard.commits += 1 }
      return {} as never
    })
    vi.mocked(flowApi.getFlow).mockImplementation(async () =>
      dto(`revision-${revisionNumber}`, 'node-stable', currentName) as never)

    await store.undo()
    await store.undo()
    expect(currentName).toBe('Original')
    await store.redo()
    await store.redo()
    expect(currentName).toBe('Second')
    expect(flowApi.patchBlock).toHaveBeenCalledTimes(4)
  })

  it('records an agent patch after its provenance advances the Flow revision', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = { ...dto('revision-1'), blocks: [] } as never
    vi.mocked(flowApi.createBlock).mockImplementation(async (_flowId, _input, guard) => {
      if (guard) { guard.revision = 'revision-2'; guard.commits += 1 }
      return { id: 'node-stable', name: 'Agent' } as never
    })
    vi.mocked(flowApi.recordAgentAction).mockImplementation(async (_flowId, _action, guard) => {
      if (guard) { guard.revision = 'revision-3'; guard.commits += 1 }
      return { recorded: 'agent-action', total: 1 }
    })
    vi.mocked(flowApi.getFlow).mockResolvedValue(dto('revision-3', 'node-stable', 'Agent') as never)

    const applied = await store.applyAgentPatch(
      { agentActionId: 'agent-action', intent: 'add node', affectedDesignIds: [] },
      [{ op: 'addBlock', block: { name: 'Agent', kind: 'function', state: 'proposed' } }],
    )
    expect(applied).toBe(true)
    expect(store.undoStack[0]?.revision).toBe('revision-3')
    expect(flowApi.recordAgentAction).toHaveBeenCalledBefore(vi.mocked(flowApi.getFlow))
  })

  it('does not report an agent patch complete when its provenance receipt fails', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = { ...dto('revision-1'), blocks: [] } as never
    vi.mocked(flowApi.createBlock).mockImplementation(async (_flowId, _input, guard) => {
      if (guard) { guard.revision = 'revision-2'; guard.commits += 1 }
      return { id: 'node-stable', name: 'Agent' } as never
    })
    vi.mocked(flowApi.recordAgentAction).mockRejectedValue(new Error('receipt unavailable'))
    vi.mocked(flowApi.getFlow).mockResolvedValue(dto('revision-2', 'node-stable', 'Agent') as never)

    const applied = await store.applyAgentPatch(
      { agentActionId: 'agent-action', intent: 'add node', affectedDesignIds: [] },
      [{ op: 'addBlock', block: { name: 'Agent', kind: 'function', state: 'proposed' } }],
    )
    expect(applied).toBe(false)
    expect(store.undoStack).toEqual([])
    expect(store.agentActions).toEqual([])
    expect((store.dto as FlowDto | null)?.blocks[0]?.name).toBe('Agent')
  })

  it('keeps surviving stable selections after an external refresh', async () => {
    const store = useFlowStore()
    store.flowId = 'flow-stable'
    store.dto = dto('revision-1', 'node-stable', 'Solver', true) as never
    store.selectedBlockId = 'node-stable'
    store.selectedPortId = 'port-stable'
    store.selectedNetId = 'net-stable'
    vi.mocked(flowApi.getFlow).mockResolvedValueOnce(dto('revision-2', 'node-stable', 'Harness', true) as never)

    expect(await store.refreshIfRevisionChanged()).toBe(true)
    expect(store.selectedBlockId).toBe('node-stable')
    expect(store.selectedPortId).toBe('port-stable')
    expect(store.selectedNetId).toBe('net-stable')
  })
})
