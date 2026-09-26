import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useFlowStore } from '../../src/stores/flow'
import type { FlowDto } from '../../src/domain/flow'

vi.mock('../../src/api/flow', () => ({ getFlow: vi.fn() }))
import * as flowApi from '../../src/api/flow'

function dto(revision: string, blockId = 'node-stable') {
  return {
    flow: { id: 'flow-stable', name: 'Circuit' },
    eda: { revision },
    blocks: [{ id: blockId, name: 'Solver', ports: [] }],
    ports: [], nets: [], bindings: [], layout: {},
  }
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
})
