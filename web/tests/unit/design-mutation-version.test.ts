import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../src/api/client', () => ({
  apiFetch: vi.fn(), apiGet: vi.fn(), apiPost: vi.fn(), qs: vi.fn(),
}))
import { apiGet, apiPost } from '../../src/api/client'
import { applyDesignMutation, setActiveDesignChange } from '../../src/api/design-lifecycle'

describe('typed UI DesignMutation adapter', () => {
  beforeEach(() => {
    setActiveDesignChange(null)
    vi.clearAllMocks()
  })

  it('sends exact live Change version and DesignRevision for every mutation', async () => {
    setActiveDesignChange('change-stable')
    vi.mocked(apiGet).mockResolvedValue({
      id: 'change-stable', version: 4, design_revision: { id: 'design-rev-4' },
    })
    vi.mocked(apiPost).mockResolvedValue({ mutation_result: { id: 'node-new' } })
    const result = await applyDesignMutation('flow', 'add_block', {
      flow_id: 'flow-stable', name: 'Node',
    })
    expect(apiGet).toHaveBeenCalledWith('/design-changes/change-stable')
    expect(apiPost).toHaveBeenCalledWith('/design-changes/change-stable/commands', {
      command: 'design_mutation', actor: 'human:ui', plane: 'flow',
      operation: 'add_block', payload: { flow_id: 'flow-stable', name: 'Node' },
      change_version: 4, expected_design_revision: 'design-rev-4',
    })
    expect(result).toEqual({ id: 'node-new' })
  })

  it('does not send a write after active Change context switches', async () => {
    setActiveDesignChange('change-old')
    vi.mocked(apiGet).mockImplementation(async () => {
      setActiveDesignChange('change-new')
      return { id: 'change-old', version: 4, design_revision: { id: 'rev-4' } }
    })
    await expect(applyDesignMutation('flow', 'add_block', {
      flow_id: 'flow-old', name: 'Node',
    })).rejects.toThrow('context changed')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('requires an active DesignChange before any read or write', async () => {
    await expect(applyDesignMutation('flow', 'add_block', {})).rejects.toThrow('active DesignChange')
    expect(apiGet).not.toHaveBeenCalled()
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('rejects a stale Flow intent instead of rebinding it to the latest DesignRevision', async () => {
    setActiveDesignChange('change-stable')
    vi.mocked(apiGet).mockResolvedValue({
      id: 'change-stable', version: 5,
      design_revision: { id: 'design-rev-5', flow_model_ref: { identity: 'flow-stable', revision: 'flow-r3' } },
    })
    const guard = { flowId: 'flow-stable', revision: 'flow-r2', commits: 0 }

    await expect(applyDesignMutation('flow', 'update_block', {
      flow_id: 'flow-stable', block_id: 'node-stable', name: 'Old intent',
    }, 'human:ui', guard)).rejects.toThrow('Flow design changed')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('binds local history to the committed Flow revision returned by the server', async () => {
    setActiveDesignChange('change-stable')
    vi.mocked(apiGet).mockResolvedValue({
      id: 'change-stable', version: 4,
      design_revision: { id: 'design-rev-4', flow_model_ref: { identity: 'flow-stable', revision: 'flow-r2' } },
    })
    vi.mocked(apiPost).mockResolvedValue({
      mutation_result: { id: 'node-stable' },
      design_revision: { id: 'design-rev-5', flow_model_ref: { identity: 'flow-stable', revision: 'flow-r3' } },
    })
    const guard = { flowId: 'flow-stable', revision: 'flow-r2', commits: 0 }

    await applyDesignMutation('flow', 'update_block', {
      flow_id: 'flow-stable', block_id: 'node-stable', name: 'Local',
    }, 'human:ui', guard)
    expect(guard).toEqual({ flowId: 'flow-stable', revision: 'flow-r3', commits: 1 })
  })
})
