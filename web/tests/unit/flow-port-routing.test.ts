import { describe, expect, it } from 'vitest'
import { FLOW_NODE_WIDTH, layoutNodePorts, x6PortGroup } from '../../src/components/flow/port-routing'

describe('Flow net port routing', () => {
  it('maps stored IN/OUT direction to registered X6 handle groups', () => {
    expect(x6PortGroup({ semanticKind: 'data', direction: 'output' })).toBe('data-out')
    expect(x6PortGroup({ semanticKind: 'data', direction: 'input' })).toBe('data-in')
    expect(x6PortGroup({ semanticKind: 'control', direction: 'input' })).toBe('control-in')
  })

  it('gives mixed-kind multiports unique side slots shared by labels and X6', () => {
    const block = { kind: 'function', state: 'proposed', metaJson: '{}', symbol: null }
    const ports = [
      { id: 'out-data', direction: 'output', semanticKind: 'data', positionOrder: 0 },
      { id: 'in-data', direction: 'input', semanticKind: 'data', positionOrder: 0 },
      { id: 'in-control', direction: 'input', semanticKind: 'control', positionOrder: 1 },
      { id: 'out-resource', direction: 'output', semanticKind: 'resource', positionOrder: 1 },
    ] as any
    const layout = layoutNodePorts(block as any, ports)
    expect(layout.inputs.map((p) => p.id)).toEqual(['in-data', 'in-control'])
    expect(layout.outputs.map((p) => p.id)).toEqual(['out-data', 'out-resource'])
    expect(layout.points['in-data'].x).toBe(0)
    expect(layout.points['out-data'].x).toBe(FLOW_NODE_WIDTH)
    expect(layout.points['in-data'].y).toBe(layout.points['out-data'].y)
    expect(layout.points['in-control'].y).toBe(layout.points['out-resource'].y)
    expect(layout.points['in-control'].y - layout.points['in-data'].y).toBeGreaterThanOrEqual(20)
    expect(layout.height).toBeGreaterThan(layout.points['in-control'].y + 10)
  })
})
