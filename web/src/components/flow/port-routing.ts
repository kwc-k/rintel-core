import type { FlowBlock, FlowPort } from '../../domain/flow'

export const FLOW_NODE_WIDTH = 230
const PORT_ROW_HEIGHT = 22

function hasDescription(block: Pick<FlowBlock, 'metaJson'>): boolean {
  try {
    return !!JSON.parse(block.metaJson || '{}')?.design?.description
  } catch {
    return false
  }
}

/** One per-side row order for both the HTML label and the X6 magnet. */
export function layoutNodePorts(
  block: Pick<FlowBlock, 'kind' | 'state' | 'metaJson' | 'symbol' | 'binding'>,
  ports: FlowPort[],
): {
  height: number
  inputs: FlowPort[]
  outputs: FlowPort[]
  points: Record<string, { x: number; y: number }>
} {
  const ordered = (direction: FlowPort['direction']) => ports
    .filter((port) => port.direction === direction)
    .sort((a, b) => (a.portContract?.ordinal ?? a.positionOrder) -
      (b.portContract?.ordinal ?? b.positionOrder) || a.id.localeCompare(b.id))
  const inputs = ordered('input')
  const outputs = ordered('output')
  const firstY = 44 + (hasDescription(block) ? 28 : 0) +
    (block.symbol?.path || (block.state === 'existing' && !block.binding) ? 13 : 0)
  const rows = Math.max(inputs.length, outputs.length)
  const height = Math.max(88, firstY + Math.max(rows - 1, 0) * PORT_ROW_HEIGHT +
    34 + (block.kind === 'composite' ? 14 : 0))
  const points: Record<string, { x: number; y: number }> = {}
  inputs.forEach((port, index) => { points[port.id] = { x: 0, y: firstY + index * PORT_ROW_HEIGHT } })
  outputs.forEach((port, index) => {
    points[port.id] = { x: FLOW_NODE_WIDTH, y: firstY + index * PORT_ROW_HEIGHT }
  })
  return { height, inputs, outputs, points }
}

/** The X6 group names are `*-in` / `*-out`, not stored `input` / `output`. */
export function x6PortGroup(port: Pick<FlowPort, 'semanticKind' | 'direction'>): string {
  return `${port.semanticKind}-${port.direction === 'input' ? 'in' : 'out'}`
}
