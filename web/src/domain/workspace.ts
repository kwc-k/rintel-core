// Canonical Entity Ref — the single global selection/focus identity
// (SPEC-P1 §8 / S2-WEB-CONTRACT §3). Pure; no Vue/Pinia imports.

export type Plane = 'evidence' | 'arch'

export type EvidenceEntityType = 'node' | 'edge' | 'file' | 'dir'

export interface EntityRef {
  plane: Plane
  entityType: string
  id: string
}

const EV_PREFIX = 'ev:'
const ARCH_PREFIX = 'ar:'

export type ArchEntityType = 'component' | 'relation' | 'mapping' | 'annotation'

/**
 * Build the canonical evidence ref:
 *   ev:{repo_id}:{node|edge|file|dir}:{canonical_id}
 * where a node's canonical_id is `node:{kind}:{qname}`, an edge's is
 * `edge:{kind}:{src}:{dst}`, and a file/dir's canonical_id is its
 * repo-relative path.
 */
export function makeEvRef(repoId: string, entityType: EvidenceEntityType, canonicalId: string): string {
  return `${EV_PREFIX}${repoId}:${entityType}:${canonicalId}`
}

export function parseEvRef(ref: string): EntityRef | null {
  if (!ref.startsWith(EV_PREFIX)) return null
  const rest = ref.slice(EV_PREFIX.length)
  const sep = rest.indexOf(':')
  if (sep <= 0) return null
  const repoId = rest.slice(0, sep)
  const tail = rest.slice(sep + 1)
  const sep2 = tail.indexOf(':')
  if (sep2 <= 0) return null
  const entityType = tail.slice(0, sep2)
  const id = tail.slice(sep2 + 1)
  if (!id) return null
  return { plane: 'evidence', entityType, id }
}

/** Return the canonical id (the part after `ev:{repo}:{entityType}:`). */
export function canonicalIdOf(ref: string): string | null {
  const parsed = parseEvRef(ref)
  return parsed ? parsed.id : null
}

export function sameRef(a: EntityRef | null, b: EntityRef | null): boolean {
  if (!a || !b) return a === b
  return a.plane === b.plane && a.entityType === b.entityType && a.id === b.id
}

/**
 * Convert an evidence-plane EntityRef into the server's mapping identity
 * (`entity_type` + `entity_id`). FILE/DIRECTORY selections map onto the
 * canonical `node:FILE:{path}` / `node:DIRECTORY:{path}` ids; nodes and edges
 * pass through unchanged. Any non-evidence ref (or an unknown evidence
 * entity type) yields null — batch creation only collects successful
 * conversions (S3-WEB-CONTRACT §3).
 */
export function evidenceEntityOf(ref: EntityRef): { entityType: 'node' | 'edge'; entityId: string } | null {
  if (ref.plane !== 'evidence') return null
  switch (ref.entityType) {
    case 'node':
      return { entityType: 'node', entityId: ref.id }
    case 'edge':
      return { entityType: 'edge', entityId: ref.id }
    case 'file':
      return { entityType: 'node', entityId: `node:FILE:${ref.id}` }
    case 'dir':
      return { entityType: 'node', entityId: `node:DIRECTORY:${ref.id}` }
    default:
      return null
  }
}

/**
 * Build a canonical arch ref:
 *   ar:{workspace_id}:{model_id}:{component|relation|mapping|annotation}:{uuid}
 * (SPEC-P1 §8 / S3-WEB-CONTRACT §3). Explicitly carries the model scope (R1).
 */
export function makeArchRef(workspaceId: string, modelId: string, entityType: ArchEntityType, id: string): string {
  return `${ARCH_PREFIX}${workspaceId}:${modelId}:${entityType}:${id}`
}

/**
 * Parse an arch ref back to an EntityRef. The workspace/model scope is not
 * retained in the EntityRef (the canonical arch id is the trailing uuid),
 * which is what focus/selection resolution needs.
 */
export function parseArchRef(ref: string): EntityRef | null {
  if (!ref.startsWith(ARCH_PREFIX)) return null
  const rest = ref.slice(ARCH_PREFIX.length)
  const parts = rest.split(':')
  // wid:mid:entityType:uuid → at least 4 non-empty segments
  if (parts.length < 4) return null
  if (parts.slice(0, 3).some((p) => p === '')) return null
  const entityType = parts[2]
  const id = parts.slice(3).join(':')
  if (!id) return null
  return { plane: 'arch', entityType, id }
}
