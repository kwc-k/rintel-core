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
})
