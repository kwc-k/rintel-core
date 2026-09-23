// Renderer-neutral Software Topology domain model (TOPO-UI0).
//
// Pure projection logic: takes the frozen-artifact bundle (server-side
// projector output) and produces a *scene* for one drill level
// (module / file / function) plus whatever the user expanded.  No X6, no
// Vue — the canvas component only renders a Scene.  All truth fields flow
// through unchanged (U1); nothing here ever upgrades INFERRED to resolved
// or mutates canonical topology (U4).

export type RelationKind = 'CALL' | 'DATA' | 'STATE' | 'CONTROL' | 'TIME' | 'RESOURCE'
export const RELATION_KINDS: RelationKind[] = ['CALL', 'DATA', 'STATE', 'CONTROL', 'TIME', 'RESOURCE']

export type CandidateKind = 'MODULE' | 'FLOW' | 'COMPOSITE'

export interface Witness {
  fact_id: string
  provider: string
  kind: string
  semantic?: string
  source: { file: string; line?: number | null; column?: number | null }
  expr?: string | null
}

export interface TopoEdge {
  kind: string
  source: string
  target: string
  truth_layer?: string
  truth_class?: string
  execution_modality?: string
  target_resolution?: string
  coverage?: string
  witness_count?: number
  representative_witnesses?: Witness[]
  guard?: string | null
  data?: { token?: string; flow?: string } | null
  source_span?: { file?: string; line?: number | null } | null
}

export interface TopoNode {
  canonical_symbol_id: string
  name: string
  language?: string
  file?: string
  repo_path?: string | null
  snapshot_id?: string
  binding_status?: string
  control_landmarks?: Array<{ semantic_kind?: string; guard?: string | null; source?: { file?: string; line?: number } }>
  resources?: string[]
  provenance?: Record<string, unknown> | null
}

export interface Suggestion {
  origin: string
  candidate_id?: string
  candidate_kind: CandidateKind
  members: string[]
  score: number | null
  score_components?: Record<string, unknown> | null
  interface_diagnostics?: Record<string, unknown> | null
  soft_features?: Record<string, unknown> | null
  hard_features?: Record<string, unknown> | null
  why: string[]
  legal?: unknown[]
  classification?: {
    declared_module_relation?: string
    declared_module?: string
    description?: string
  } | null
  confidence?: number | null
  witnesses?: Array<Record<string, unknown>>
  rejection_reasons?: string[]
  boundary?: {
    data_in?: unknown[]
    data_out?: unknown[]
    state_inout?: unknown[]
    resource_ports?: unknown[]
    calls_in?: unknown[]
    calls_out?: unknown[]
  } | null
  truth_class: string
  data_capability?: string
}

export interface TopoMeta {
  label: string
  languages: string[]
  data_capability: string
  synthetic: boolean
  capability_notes: string[]
  facts_total?: number | null
  symbols_total?: number | null
  bound_symbols?: number | null
  unbound_symbols?: number | null
  snapshot_id?: string | null
  resources: string[]
  placeholders: string[]
  overlay_stats: Record<string, number>
  source_repo_id: string | null
  source_root: string | null
  suggestion_origin: string
}

export interface RouteCCommunity {
  community_id: string
  members: string[]
  size: number
  legal_status?: string
  rejection_reasons?: string[]
  score?: number | null
  why?: string[]
}

export interface RouteCDebug {
  origin: string
  production: false
  modularity?: number | null
  legal_count?: number | null
  communities?: RouteCCommunity[] | null
}

export interface TopoBundle {
  lane: string
  meta: TopoMeta
  nodes: TopoNode[]
  edges: TopoEdge[]
  declared_modules: string[]
  cross_cutting: string[]
  suggestions: Suggestion[]
  route_c: RouteCDebug | null
}

// ---------------------------------------------------------------------------
// scene model
// ---------------------------------------------------------------------------

export type SceneNodeKind =
  | 'module'          // declared module
  | 'suggested-module'
  | 'suggested-flow'
  | 'suggested-composite'
  | 'file'
  | 'function'
  | 'resource'
  | 'unknown'
  | 'external'        // honest [External] stub: the other end is outside scope

export interface SceneNode {
  id: string
  kind: SceneNodeKind
  label: string
  sub: string
  // declared module
  fnCount?: number
  fileCount?: number
  crossCuttingCount?: number
  resources?: string[]
  badge?: Suggestion | null          // exact-aligned suggestion
  suggestionIdx?: number
  score?: number | null
  confidence?: number | null
  declRelation?: string
  declModule?: string
  // file
  file?: string
  repoPath?: string | null
  // function
  canonicalId?: string
  language?: string
  isCrossCutting?: boolean
  truth?: { target_resolution: string; coverage: string }
}

export interface SceneEdge {
  id: string
  kinds: Partial<Record<RelationKind, number>>
  source: string
  target: string
  raw: TopoEdge[]              // member edges (drill-down directly available)
  truthSummary: string
  modalitySummary: string
}

export interface Scene {
  level: 1 | 2 | 3
  path: ContainerRef[]
  nodes: SceneNode[]
  edges: SceneEdge[]
}

export interface ContainerRef {
  kind: 'root' | 'module' | 'sugg' | 'file'
  id: string
  label: string
}

export interface ProjectOptions {
  path: ContainerRef[]                 // [] = module level
  overlays: Set<RelationKind>
  hideCrossCutting: boolean
}

export const UNKNOWN_TARGET_ID = 'unknown-dynamic'

// ---------------------------------------------------------------------------
// index over the bundle (built once per lane)
// ---------------------------------------------------------------------------

export interface TopoIndex {
  nodeById: Map<string, TopoNode>
  fnIdsByFile: Map<string, string[]>
  fileOf: Map<string, string>
  filesByModule: Map<string, string[]>
  fnIdsByModule: Map<string, string[]>
  modOfFn: Map<string, string>
  cc: Set<string>
  alignedSuggByModule: Map<string, Suggestion>
  separateSuggestions: Suggestion[]
  resources: Set<string>
  hasUnknown: boolean
  suggFiles: Map<number, string[]>     // suggestion idx -> distinct files
  suggModule: Map<number, string | null> // suggestion idx -> aligned declared module or null
}

export function declaredModuleOf(node: TopoNode): string | null {
  const file = node.file ?? ''
  const parts = file.split('/').filter(Boolean)
  return parts.length ? parts[0] : null
}

export function buildIndex(bundle: TopoBundle): TopoIndex {
  const nodeById = new Map<string, TopoNode>()
  const fnIdsByFile = new Map<string, string[]>()
  const fileOf = new Map<string, string>()
  const filesByModule = new Map<string, string[]>()
  const fnIdsByModule = new Map<string, string[]>()
  const modOfFn = new Map<string, string>()
  const cc = new Set(bundle.cross_cutting)
  const resources = new Set<string>(bundle.meta.resources)

  for (const n of bundle.nodes) {
    nodeById.set(n.canonical_symbol_id, n)
    const file = n.file ?? ''
    if (file) {
      fileOf.set(n.canonical_symbol_id, file)
      if (!fnIdsByFile.has(file)) fnIdsByFile.set(file, [])
      fnIdsByFile.get(file)!.push(n.canonical_symbol_id)
    }
    const mod = declaredModuleOf(n)
    if (mod) {
      modOfFn.set(n.canonical_symbol_id, mod)
      if (!fnIdsByModule.has(mod)) fnIdsByModule.set(mod, [])
      fnIdsByModule.get(mod)!.push(n.canonical_symbol_id)
      if (!filesByModule.has(mod)) filesByModule.set(mod, [])
      const file = n.file ?? ''
      if (file && !filesByModule.get(mod)!.includes(file)) {
        filesByModule.get(mod)!.push(file)
      }
    }
    for (const r of n.resources ?? []) resources.add(r)
  }

  const alignedSuggByModule = new Map<string, Suggestion>()
  const separateSuggestions: Suggestion[] = []
  for (const s of bundle.suggestions) {
    // aligned = every member belongs to ONE declared module (upstream
    // classification semantics: "all members belong to one declared
    // package" — includes subsets).  Crossing suggestions stay separate.
    const mods = new Set(s.members.map((m) => modOfFn.get(m)).filter((m): m is string => !!m))
    const aligned = mods.size === 1 ? mods.values().next().value! : null
    if (aligned) alignedSuggByModule.set(aligned, s)
    else separateSuggestions.push(s)
  }

  // distinct files per suggestion (stable order: sorted by file)
  const suggFiles = new Map<number, string[]>()
  const suggModule = new Map<number, string | null>()
  bundle.suggestions.forEach((s, i) => {
    const mods = new Set(s.members.map((m) => modOfFn.get(m)).filter((m): m is string => !!m))
    const aligned = mods.size === 1 ? mods.values().next().value! : null
    suggModule.set(i, aligned)
    const files = new Set<string>()
    for (const m of s.members) {
      const f = fileOf.get(m)
      if (f) files.add(f)
    }
    suggFiles.set(i, [...files].sort())
  })

  return {
    nodeById, fnIdsByFile, fileOf, filesByModule, fnIdsByModule, modOfFn,
    cc, alignedSuggByModule, separateSuggestions, resources,
    hasUnknown: bundle.edges.some(
      (e) => e.target === '[Unknown Dynamic Target]'),
    suggFiles, suggModule,
  }
}

// ---------------------------------------------------------------------------
// capability honesty (WORKBENCH §8-§9)
// ---------------------------------------------------------------------------

export type CapabilityLevel = 'COMPLETE' | 'PARTIAL' | 'UNKNOWN' | 'MIXED'

export interface RelationCapability {
  kind: RelationKind
  capability: CapabilityLevel
  total: number          // edges of this kind in the frozen bundle
  complete: number       // edges with coverage=COMPLETE
  unknownCov: number     // edges with coverage=UNKNOWN
  why: string[]          // evidence-backed reasons (never invented)
}

/**
 * Per-relation-type capability, derived ONLY from the frozen artifacts:
 *
 *  - DATA keeps `meta.data_capability` verbatim (PARTIAL/MIXED/COMPLETE).
 *    Known limits stay: C cross-procedural DATA = PARTIAL (FAC), no
 *    resolved DATA wires upstream (JPL).  Never upgraded by the UI.
 *  - other kinds use the frozen edge set: 0 edges => UNKNOWN (the
 *    analyzer lane produced no such relations — the UI never invents
 *    them); all-resolved => COMPLETE; any coverage=UNKNOWN => PARTIAL.
 */
export function relationCapabilities(bundle: TopoBundle): RelationCapability[] {
  const meta = bundle.meta
  const stats: Record<RelationKind, { total: number; complete: number; unknownCov: number }> = {
    CALL: { total: 0, complete: 0, unknownCov: 0 },
    DATA: { total: 0, complete: 0, unknownCov: 0 },
    STATE: { total: 0, complete: 0, unknownCov: 0 },
    CONTROL: { total: 0, complete: 0, unknownCov: 0 },
    TIME: { total: 0, complete: 0, unknownCov: 0 },
    RESOURCE: { total: 0, complete: 0, unknownCov: 0 },
  }
  for (const e of bundle.edges) {
    const s = stats[e.kind as RelationKind]
    if (!s) continue
    s.total += 1
    if (e.coverage === 'COMPLETE') s.complete += 1
    else if (e.coverage === 'UNKNOWN') s.unknownCov += 1
  }
  const note = (sub: string) => meta.capability_notes.find((n) => n.includes(sub)) ?? null

  return RELATION_KINDS.map((kind) => {
    const s = stats[kind]
    let capability: CapabilityLevel
    const why: string[] = []

    if (kind === 'DATA') {
      capability = meta.data_capability as CapabilityLevel
      const n = note('DATA')
      if (n) why.push(n)
      why.push(
        meta.data_capability === 'COMPLETE'
          ? 'DATA capability COMPLETE: verbatim from the lane artifact.'
          : `DATA capability ${meta.data_capability}: verbatim from the lane artifact (never upgraded by the UI).`,
      )
    } else if (s.total === 0) {
      capability = 'UNKNOWN'
      const n = note(kind)
      if (n) why.push(n)
      why.push(
        `No ${kind} edges exist in the frozen topology; the analyzer lane `
        + `produced none (partial-coverage UI must not invent them).`,
      )
    } else if (s.unknownCov === 0) {
      capability = 'COMPLETE'
      why.push(`${s.total} ${kind} edges, all with coverage=COMPLETE.`)
    } else {
      capability = 'PARTIAL'
      why.push(
        `${s.total} ${kind} edges; ${s.unknownCov} resolved with `
        + `coverage=UNKNOWN (unresolved or dynamic targets).`,
      )
    }
    return { kind, capability, total: s.total, complete: s.complete, unknownCov: s.unknownCov, why }
  })
}

// ---------------------------------------------------------------------------
// projection
// ---------------------------------------------------------------------------

function resourceNameOf(e: TopoEdge): string {
  return e.target
}

function containerOfFn(cid: string, idx: TopoIndex): string | null {
  const mod = idx.modOfFn.get(cid)
  return mod ? `module:${mod}` : null
}

/** Like containerOfFn, but keeps a stub id for file-less symbols so the
 * edge stays visible (never floating) instead of being dropped. */
function containerOrStub(cid: string, idx: TopoIndex): string | null {
  const mod = idx.modOfFn.get(cid)
  if (mod) return `module:${mod}`
  return idx.nodeById.has(cid) ? `ext:node:${cid}` : null
}

function suggestionName(s: Suggestion, idx: TopoIndex, i: number): string {
  if (s.classification?.declared_module) return s.classification.declared_module
  const first = s.members[0]
  const mod = first ? idx.modOfFn.get(first) : null
  return mod ? `(crossing) ${mod}…` : `Suggested ${i + 1}`
}

function suggestedKind(s: Suggestion): SceneNodeKind {
  if (s.candidate_kind === 'FLOW') return 'suggested-flow'
  if (s.candidate_kind === 'COMPOSITE') return 'suggested-composite'
  return 'suggested-module'
}

function truthSummary(edges: TopoEdge[]): string {
  const counts = new Map<string, number>()
  for (const e of edges) {
    const key = e.truth_class ?? 'UNKNOWN'
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const parts = [...counts.entries()].sort((a, b) => b[1] - a[1])
  if (parts.length === 1) return parts[0][0]
  return parts.map(([k, v]) => `${k}×${v}`).join(', ')
}

function modalitySummary(edges: TopoEdge[]): string {
  const counts = new Map<string, number>()
  for (const e of edges) {
    const key = e.execution_modality ?? 'UNKNOWN'
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const parts = [...counts.entries()].sort((a, b) => b[1] - a[1])
  if (parts.length === 1) return parts[0][0]
  return parts.map(([k, v]) => `${k}×${v}`).join(', ')
}

function nodeVisible(n: TopoNode, cc: Set<string>, hideCC: boolean): boolean {
  return !(hideCC && cc.has(n.canonical_symbol_id))
}

function edgeVisible(e: TopoEdge, overlays: Set<RelationKind>, idx: TopoIndex, hideCC: boolean): boolean {
  if (!overlays.has(e.kind as RelationKind)) return false
  if (hideCC) {
    const s = idx.nodeById.get(e.source)
    const t = idx.nodeById.get(e.target)
    if (s && idx.cc.has(s.canonical_symbol_id)) return false
    if (t && idx.cc.has(t.canonical_symbol_id)) return false
  }
  return true
}

function groupEdges(
  edges: TopoEdge[],
  overlays: Set<RelationKind>,
  idx: TopoIndex,
  hideCC: boolean,
  srcGroup: (e: TopoEdge) => string | null,
  tgtGroup: (e: TopoEdge) => string | null,
): SceneEdge[] {
  const groups = new Map<string, { kinds: Partial<Record<RelationKind, number>>; raw: TopoEdge[] }>()
  for (const e of edges) {
    if (!edgeVisible(e, overlays, idx, hideCC)) continue
    const s = srcGroup(e)
    const t = tgtGroup(e)
    if (!s || !t || s === t) continue          // internal edges stay internal
    const key = `${s}\u0000${t}`
    const kind = e.kind as RelationKind
    if (!groups.has(key)) groups.set(key, { kinds: {}, raw: [] })
    const g = groups.get(key)!
    g.kinds[kind] = (g.kinds[kind] ?? 0) + 1
    g.raw.push(e)
  }
  return [...groups.entries()].map(([key, g]) => {
    const [s, t] = key.split('\u0000')
    return {
      id: `me:${s}->${t}`,
      kinds: g.kinds,
      source: s,
      target: t,
      raw: g.raw,
      truthSummary: truthSummary(g.raw),
      modalitySummary: modalitySummary(g.raw),
    }
  })
}

/** Module-level scene: declared modules + suggested + resources + unknown. */
export function projectModuleScene(bundle: TopoBundle, idx: TopoIndex, opts: ProjectOptions): Scene {
  const nodes: SceneNode[] = []
  const byId = new Map<string, SceneNode>()

  for (const mod of bundle.declared_modules) {
    const fns = idx.fnIdsByModule.get(mod) ?? []
    const files = idx.filesByModule.get(mod) ?? []
    const ccCount = fns.filter((f) => idx.cc.has(f)).length
    const resSet = new Set<string>()
    for (const f of fns) for (const r of idx.nodeById.get(f)?.resources ?? []) resSet.add(r)
    const badge = idx.alignedSuggByModule.get(mod) ?? null
    const badgeNote = badge && badge.members.length < fns.length
      ? ` · suggested subset ${badge.members.length}/${fns.length}` : ''
    const node: SceneNode = {
      id: `module:${mod}`,
      kind: 'module',
      label: mod,
      sub: `${fns.length} functions · ${files.length} files${ccCount ? ` · ${ccCount} cross-cutting` : ''}${badgeNote}`,
      fnCount: fns.length,
      fileCount: files.length,
      crossCuttingCount: ccCount,
      resources: [...resSet].sort(),
      badge,
      score: badge?.score ?? null,
      confidence: badge?.confidence ?? null,
      declRelation: badge?.classification?.declared_module_relation,
      declModule: mod,
    }
    nodes.push(node)
    byId.set(node.id, node)
  }

  idx.separateSuggestions.forEach((s, j) => {
    const i = bundle.suggestions.indexOf(s)
    const node: SceneNode = {
      id: `sugg:${i}`,
      kind: suggestedKind(s),
      label: suggestionName(s, idx, i),
      sub: `${s.members.length} functions · ${idx.suggFiles.get(i)?.length ?? 0} files`,
      suggestionIdx: i,
      score: s.score,
      confidence: s.confidence,
      declRelation: s.classification?.declared_module_relation,
      declModule: s.classification?.declared_module,
      badge: null,
      fnCount: s.members.length,
      fileCount: idx.suggFiles.get(i)?.length,
    }
    nodes.push(node)
    byId.set(node.id, node)
  })

  // resource + unknown nodes derived from aggregated edges (no dangling nodes)
  const edges = groupEdges(
    bundle.edges, opts.overlays, idx, opts.hideCrossCutting,
    (e) => {
      if (e.kind === 'RESOURCE' || e.target === '[Unknown Dynamic Target]') {
        return e.source === '[Unknown Dynamic Target]' ? null : containerOrStub(e.source, idx)
      }
      return containerOrStub(e.source, idx)
    },
    (e) => {
      if (e.target === '[Unknown Dynamic Target]') return UNKNOWN_TARGET_ID
      if (e.kind === 'RESOURCE') return `resource:${resourceNameOf(e)}`
      const c = containerOfFn(e.target, idx)
      return c ?? `ext:node:${e.target}`
    },
  )
  // WORKBENCH §13: no floating edge — every visible edge gets a visible
  // [External] stub when its other end lies outside the declared set.
  // (resource:/unknown-dynamic ends are handled by their own node kinds.)
  for (const e of edges) {
    if (!byId.has(e.target) && !e.target.startsWith('resource:') && e.target !== UNKNOWN_TARGET_ID) {
      const n = idx.nodeById.get(e.target.replace(/^ext:node:/, '')) ?? null
      byId.set(e.target, {
        id: e.target,
        kind: 'external',
        label: `[External] ${e.target.replace(/^module:/, '').replace(/^ext:node:/, '')}`,
        sub: e.target.startsWith('module:')
          ? 'outside declared modules'
          : n ? `no declared container · ${n.name}` : 'no declared container',
      })
      nodes.push(byId.get(e.target)!)
    }
    if (!byId.has(e.source) && !e.source.startsWith('resource:') && e.source !== UNKNOWN_TARGET_ID) {
      byId.set(e.source, {
        id: e.source,
        kind: 'external',
        label: `[External] ${e.source.replace(/^ext:node:/, '')}`,
        sub: 'no declared container',
      })
      nodes.push(byId.get(e.source)!)
    }
  }
  for (const e of edges) {
    if (e.target.startsWith('resource:') && !byId.has(e.target)) {
      const rname = e.target.slice('resource:'.length)
      const node: SceneNode = { id: e.target, kind: 'resource', label: rname, sub: 'resource port' }
      byId.set(node.id, node)
      nodes.push(node)
    } else if (e.target === UNKNOWN_TARGET_ID && !byId.has(UNKNOWN_TARGET_ID)) {
      const node: SceneNode = {
        id: UNKNOWN_TARGET_ID,
        kind: 'unknown',
        label: '[Unknown Dynamic Target]',
        sub: 'target_resolution = UNKNOWN',
        truth: { target_resolution: 'UNKNOWN', coverage: 'UNKNOWN/PARTIAL' },
      }
      byId.set(node.id, node)
      nodes.push(node)
    }
  }

  return { level: 1, path: [], nodes, edges }
}

/** File-level scene of one expanded container (module or suggestion). */
export function projectFileScene(
  bundle: TopoBundle, idx: TopoIndex, opts: ProjectOptions, container: ContainerRef,
): Scene {
  const files: string[] = []
  if (container.kind === 'module') {
    files.push(...(idx.filesByModule.get(container.id) ?? []))
  } else if (container.kind === 'sugg') {
    files.push(...(idx.suggFiles.get(Number(container.id)) ?? []))
  } else {
    throw new Error(`container ${container.kind} has no file children`)
  }
  const fileSet = new Set(files)
  const nodes: SceneNode[] = files.map((f) => {
    const fns = idx.fnIdsByFile.get(f) ?? []
    const ccCount = fns.filter((x) => idx.cc.has(x)).length
    const repoPath = fns.length ? idx.nodeById.get(fns[0])?.repo_path ?? null : null
    return {
      id: `file:${f}`,
      kind: 'file',
      label: f.split('/').slice(-1)[0],
      sub: `${fns.length} functions${ccCount ? ` · ${ccCount} cross-cutting` : ''}`,
      file: f,
      repoPath,
      fnCount: fns.length,
      crossCuttingCount: ccCount,
    }
  })
  const byId = new Map(nodes.map((n) => [n.id, n]))

  const fnGroup = (cid: string): string | null => {
    const f = idx.fileOf.get(cid)
    if (!f) return null
    return fileSet.has(f) ? `file:${f}` : null
  }
  // external container of a member symbol (its file outside the open module)
  const extFileOf = (cid: string): string | null => {
    const f = idx.fileOf.get(cid)
    return f ? `ext:file:${f}` : `ext:fn:${cid}`
  }
  const edges = groupEdges(
    bundle.edges, opts.overlays, idx, opts.hideCrossCutting,
    (e) => {
      if (e.kind === 'RESOURCE' || e.target === '[Unknown Dynamic Target]') {
        return e.source === '[Unknown Dynamic Target]' ? null : (fnGroup(e.source) ?? extFileOf(e.source))
      }
      return fnGroup(e.source) ?? extFileOf(e.source)
    },
    (e) => {
      if (e.target === '[Unknown Dynamic Target]') return UNKNOWN_TARGET_ID
      if (e.kind === 'RESOURCE') return `resource:${resourceNameOf(e)}`
      return fnGroup(e.target) ?? extFileOf(e.target)
    },
  )
  // WORKBENCH §13: visible edge ⇒ visible stub on both ends.
  for (const e of edges) {
    if (!byId.has(e.target) && !e.target.startsWith('resource:') && e.target !== UNKNOWN_TARGET_ID) {
      const f = e.target.replace(/^ext:file:/, '')
      byId.set(e.target, {
        id: e.target,
        kind: 'external',
        label: `[External] ${f.split('/').slice(-1)[0]}`,
        sub: e.target.startsWith('ext:file:')
          ? 'outside this module'
          : (idx.nodeById.get(e.target.replace(/^ext:fn:/, ''))?.name ?? 'no host file'),
      })
      nodes.push(byId.get(e.target)!)
    }
    if (!byId.has(e.source) && !e.source.startsWith('resource:') && e.source !== UNKNOWN_TARGET_ID) {
      const f = e.source.replace(/^ext:file:/, '')
      byId.set(e.source, {
        id: e.source,
        kind: 'external',
        label: `[External] ${f.split('/').slice(-1)[0]}`,
        sub: 'outside this module',
      })
      nodes.push(byId.get(e.source)!)
    }
  }
  for (const e of edges) {
    if (e.target.startsWith('resource:') && !byId.has(e.target)) {
      const rname = e.target.slice('resource:'.length)
      const node: SceneNode = { id: e.target, kind: 'resource', label: rname, sub: 'resource port' }
      byId.set(node.id, node)
      nodes.push(node)
    } else if (e.target === UNKNOWN_TARGET_ID && !byId.has(UNKNOWN_TARGET_ID)) {
      const node: SceneNode = {
        id: UNKNOWN_TARGET_ID,
        kind: 'unknown',
        label: '[Unknown Dynamic Target]',
        sub: 'target_resolution = UNKNOWN',
        truth: { target_resolution: 'UNKNOWN', coverage: 'UNKNOWN/PARTIAL' },
      }
      byId.set(node.id, node)
      nodes.push(node)
    }
  }
  return { level: 2, path: [container], nodes, edges }
}

/** Function-level scene: raw edges of one expanded file. */
export function projectFunctionScene(
  bundle: TopoBundle, idx: TopoIndex, opts: ProjectOptions, fileRef: ContainerRef,
): Scene {
  const file = fileRef.id
  const fns = idx.fnIdsByFile.get(file) ?? []
  const fnSet = new Set(fns)
  const nodes: SceneNode[] = fns.map((cid) => {
    const n = idx.nodeById.get(cid)
    const isCC = idx.cc.has(cid)
    return {
      id: cid,
      kind: 'function',
      label: n?.name ?? cid.split(':').pop() ?? cid,
      sub: `${file}${isCC ? ' · cross-cutting' : ''}`,
      canonicalId: cid,
      file,
      repoPath: n?.repo_path ?? null,
      language: n?.language,
      isCrossCutting: isCC,
    }
  })
  const byId = new Map(nodes.map((n) => [n.id, n]))

  // raw (non-aggregated) edges among displayed functions + resources/unknown
  // WORKBENCH §13: no floating edges — out-of-scope ends become [External]
  // stubs; an edge with NEITHER end in view stays hidden.
  const extStubOf = (cid: string): string => {
    const f = idx.fileOf.get(cid)
    return f ? `ext:file:${f}` : `ext:fn:${cid}`
  }
  const ensureExt = (id: string): void => {
    if (byId.has(id)) return
    const f = id.replace(/^ext:file:/, '')
    byId.set(id, id.startsWith('ext:file:')
      ? {
          id, kind: 'external', label: `[External] ${f.split('/').slice(-1)[0]}`,
          sub: 'outside this file',
        }
      : {
          id, kind: 'external',
          label: `[External] ${idx.nodeById.get(id.replace(/^ext:fn:/, ''))?.name ?? id}`,
          sub: 'outside this file · no host file',
        })
    nodes.push(byId.get(id)!)
  }
  const edges: SceneEdge[] = []
  for (const e of bundle.edges) {
    if (!edgeVisible(e, opts.overlays, idx, opts.hideCrossCutting)) continue
    const srcFn = e.source === '[Unknown Dynamic Target]' ? null : e.source
    const tgtFn = e.target === '[Unknown Dynamic Target]' ? null : e.target
    if (e.kind === 'RESOURCE') {
      if (!srcFn || !fnSet.has(srcFn)) continue
      const rname = resourceNameOf(e)
      if (!byId.has(`resource:${rname}`)) {
        const node: SceneNode = { id: `resource:${rname}`, kind: 'resource', label: rname, sub: 'resource port' }
        byId.set(node.id, node)
        nodes.push(node)
      }
      edges.push({
        id: `fe:${e.source}->${rname}:${e.kind}`,
        kinds: { [e.kind as RelationKind]: 1 },
        source: e.source, target: `resource:${rname}`,
        raw: [e], truthSummary: truthSummary([e]), modalitySummary: modalitySummary([e]),
      })
      continue
    }
    if (e.target === '[Unknown Dynamic Target]') {
      if (!srcFn || !fnSet.has(srcFn)) continue
      if (!byId.has(UNKNOWN_TARGET_ID)) {
        const node: SceneNode = {
          id: UNKNOWN_TARGET_ID, kind: 'unknown', label: '[Unknown Dynamic Target]',
          sub: 'target_resolution = UNKNOWN',
          truth: { target_resolution: 'UNKNOWN', coverage: 'UNKNOWN/PARTIAL' },
        }
        byId.set(node.id, node)
        nodes.push(node)
      }
      edges.push({
        id: `fe:${e.source}->${UNKNOWN_TARGET_ID}:${e.kind}`,
        kinds: { [e.kind as RelationKind]: 1 },
        source: e.source, target: UNKNOWN_TARGET_ID,
        raw: [e], truthSummary: truthSummary([e]), modalitySummary: modalitySummary([e]),
      })
      continue
    }
    if (!srcFn || !tgtFn) continue
    if (!fnSet.has(srcFn) && !fnSet.has(tgtFn)) continue
    const sId = fnSet.has(srcFn) ? srcFn : extStubOf(srcFn)
    const tId = fnSet.has(tgtFn) ? tgtFn : extStubOf(tgtFn)
    if (sId !== srcFn) ensureExt(sId)
    if (tId !== tgtFn) ensureExt(tId)
    edges.push({
      id: `fe:${e.source}->${e.target}:${e.kind}:${e.truth_class ?? ''}`,
      kinds: { [e.kind as RelationKind]: 1 },
      source: sId, target: tId,
      raw: [e], truthSummary: truthSummary([e]), modalitySummary: modalitySummary([e]),
    })
  }
  return { level: 3, path: [fileRef], nodes, edges }
}

export function projectScene(bundle: TopoBundle, idx: TopoIndex, opts: ProjectOptions): Scene {
  if (opts.path.length === 0) return projectModuleScene(bundle, idx, opts)
  const last = opts.path[opts.path.length - 1]
  if (last.kind === 'module' || last.kind === 'sugg') {
    return projectFileScene(bundle, idx, opts, last)
  }
  return projectFunctionScene(bundle, idx, opts, last)
}

export function sceneKinds(edges: SceneEdge[]): RelationKind[] {
  const kinds = new Set<RelationKind>()
  for (const e of edges) for (const k of Object.keys(e.kinds) as RelationKind[]) kinds.add(k)
  return RELATION_KINDS.filter((k) => kinds.has(k))
}

/** Repo-relative path from an absolute witness source file. */
export function repoPathOf(hint: string | undefined | null, meta: TopoMeta): string | null {
  if (!hint) return null
  if (!hint.startsWith('/')) return hint
  const root = meta.source_root
  if (!root) return null
  if (hint.startsWith(root + '/')) return hint.slice(root.length + 1)
  return null
}

/**
 * Resolve a witness to a repo-relative source path.  Upstream witnesses
 * (JPL) often carry only the repo root as source.file; fall back to the
 * call-site node's file, then to a fact_id-embedded path.  Never
 * fabricate: null means the Monaco link is hidden (U5/U6).
 */
export function resolveWitnessFile(
  w: Witness, raw: TopoEdge, meta: TopoMeta, idx: TopoIndex,
): string | null {
  const direct = repoPathOf(w.source?.file, meta)
  if (direct) return direct
  const srcNode = idx.nodeById.get(raw.source)
  const fallback = srcNode?.repo_path ?? (srcNode?.file ? repoPathOf(srcNode.file, meta) : null)
  if (fallback) return fallback
  const parts = (w.fact_id ?? '').split(':')
  if (parts.length >= 3 && /\.(py|c|h|f|f90)$/.test(parts[2] ?? '')) return parts[2]
  return null
}
