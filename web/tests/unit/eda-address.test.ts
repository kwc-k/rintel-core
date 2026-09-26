import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import FlowBlockNode from '../../src/components/flow/FlowBlockNode.vue'

describe('Flow Design EDA address display', () => {
  it('shows revision-scoped labels while keeping stable IDs on port targets', () => {
    const block = {
      id: 'node-stable', flowModelId: 'flow-stable', kind: 'function',
      state: 'proposed', name: 'Solver', metaJson: '{}',
      displayAddress: 'L2.N3',
    }
    const ports = [
      { id: 'port-in-stable', blockId: block.id, name: 'data', direction: 'input',
        semanticKind: 'data', positionOrder: 0, portContract: { ordinal: 1 } },
      { id: 'port-out-stable', blockId: block.id, name: 'data', direction: 'output',
        semanticKind: 'data', positionOrder: 0, portContract: { ordinal: 1 } },
    ]
    const wrapper = mount(FlowBlockNode, {
      props: { node: { data: { block, ports } }, graph: {} },
    })
    expect(wrapper.text()).toContain('L2.N3 · Solver')
    expect(wrapper.text()).toContain('IN1 · data')
    expect(wrapper.text()).toContain('OUT1 · data')
    expect(wrapper.find('[data-port-id="port-in-stable"]').exists()).toBe(true)
    expect(wrapper.find('[data-port-id="port-out-stable"]').exists()).toBe(true)
  })

  it('opens a type card from a variable name across multiple IN/OUT ports', async () => {
    const block = { id: 'node-stable', flowModelId: 'flow-stable', kind: 'function',
      state: 'proposed', name: 'Solver', metaJson: '{}', displayAddress: 'L2.N3' }
    const contract = (id: string, ordinal: number, direction: 'IN' | 'OUT', dtype: string) => ({
      version: 'port-contract/v0', authority: 'DESIGN_ANNOTATION', port_id: id,
      owner_node_id: block.id, direction, ordinal, name: id,
      semantic_kind: 'data', port_family: 'UNKNOWN', semantic_object: 'UNKNOWN',
      generic_type: dtype === 'float64' ? 'MATRIX' : 'UNKNOWN', dtype,
      rank: 'UNKNOWN', shape: dtype === 'float64' ? '[20,20]' : 'UNKNOWN',
      unknown_fields: dtype === 'float64' ? ['rank'] : ['generic_type', 'dtype', 'shape'],
    })
    const ports = [
      { id: 'a', blockId: block.id, name: 'A', direction: 'input', semanticKind: 'data',
        codeType: 'double[20][20]', positionOrder: 0, portContract: contract('a', 1, 'IN', 'float64') },
      { id: 'rhs', blockId: block.id, name: 'rhs', direction: 'input', semanticKind: 'data',
        positionOrder: 1, portContract: contract('rhs', 2, 'IN', 'UNKNOWN') },
      { id: 'x', blockId: block.id, name: 'x', direction: 'output', semanticKind: 'data',
        positionOrder: 0, portContract: contract('x', 1, 'OUT', 'UNKNOWN') },
      { id: 'status', blockId: block.id, name: 'status', direction: 'output', semanticKind: 'data',
        positionOrder: 1, portContract: contract('status', 2, 'OUT', 'UNKNOWN') },
    ]
    const wrapper = mount(FlowBlockNode, {
      props: { node: { data: { block, ports } }, graph: {} }, attachTo: document.body,
    })
    expect(wrapper.text()).toContain('IN2 · rhs')
    expect(wrapper.text()).toContain('OUT2 · status')
    await wrapper.get('[data-testid="port-name-a"]').trigger('click')
    const popover = document.querySelector('[data-testid="port-type-popover"]')
    expect(popover?.textContent).toContain('Declared code type (Design): double[20][20]')
    expect(popover?.textContent).toContain('Expected dtype (Design): float64')
    expect(popover?.textContent).toContain('Actual: UNKNOWN')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(document.querySelector('[data-testid="port-type-popover"]')).toBeNull()
    wrapper.unmount()
  })
})
