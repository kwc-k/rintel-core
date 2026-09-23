// C1: renderer-neutral view model (SPEC-P1 §8 / S2-WEB-CONTRACT §3).
// S2 does not draw graphs — these types are used by simple list/table
// renderers and keep the door open for a later canvas adapter. Pure; no
// Vue/Pinia/Sigma/Vue Flow imports.

import type { NeighborhoodResult } from './evidence'

export interface ViewNode {
  id: string
  label: string
  kind: string
  group?: string
  position?: { x: number; y: number }
  ghost?: boolean
  data: Record<string, unknown>
}

export interface ViewEdge {
  id: string
  source: string
  target: string
  kind: string
  label?: string
  ghost?: boolean
  data: Record<string, unknown>
}

export interface GraphViewModel {
  nodes: ViewNode[]
  edges: ViewEdge[]
}

export function neighborhoodToView(result: NeighborhoodResult): GraphViewModel {
  return {
    nodes: result.nodes.map((n) => ({
      id: n.id,
      label: n.name || n.qname,
      kind: n.kind,
      group: n.path,
      data: { ...n },
    })),
    edges: result.edges.map((e) => ({
      id: e.id,
      source: e.srcId,
      target: e.dstId,
      kind: e.kind,
      label: e.kind,
      data: { confidence: e.confidence },
    })),
  }
}
