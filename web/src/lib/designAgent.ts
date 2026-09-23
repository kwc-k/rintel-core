// TOPO-EDITOR-UX0 §7-§10/§21/§26: local design assistant (rule-based v1).
// Deterministic, testable, no LLM call.  All outputs are *proposals* —
// the UI must Preview them and the user must explicitly Accept before the
// store writes anything to TO-BE.  The agent never touches source code or
// canonical evidence (writeback stays DRC→LVS→Synthesis-owned).
import type {
  AgentProposal, DesignAnnotation, FlowBlock, FlowNet, FlowPort,
  NetKind, PortDirection, SemanticKind, TopologyPatchOp,
} from '../domain/flow'

function uuid(): string {
  return (crypto as any).randomUUID?.()
    ?? `ag-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

const KIND_LABEL: Record<string, string> = {
  function: '函数', object: '对象', composite: '组合组件', proposed: '设计',
}

// --- naming ---------------------------------------------------------------
// Heuristic: from member/block names, find the shared stem and map common
// verbs to Chinese role names (deterministic word list).
const VERB_ROLES: Array<[RegExp, string]> = [
  [/fetch|load|pull|get|read|采集|下载|获取/i, '数据采集'],
  [/normaliz|clean|sanitiz|预处理|清洗|规范化/i, '预处理'],
  [/validat|check|verify|校验|验证|检查/i, '校验'],
  [/analyz|compute|calc|分析|计算|统计/i, '分析'],
  [/transform|convert|map|转换|映射/i, '转换'],
  [/store|save|persist|cache|存储|缓存|持久化/i, '存储'],
  [/notify|send|push|publish|通知|发送|推送/i, '通知'],
  [/auth|login|token|鉴权|登录|认证/i, '鉴权'],
  [/retry|rate|throttle|限流|重试/i, '限流'],
]

function sharedStem(names: string[]): string | null {
  if (names.length === 0) return null
  const parts = names.map((n) => n.replace(/[_-]+/g, ' ').split(/\s+/))
  let stem = parts[0]
  for (const p of parts.slice(1)) {
    stem = stem.filter((word, i) => p[i] === word)
    if (stem.length === 0) break
  }
  return stem.length ? stem.join(' ') : null
}

export function suggestName(names: string[], extra?: {
  ports?: Array<{ name: string }>
  kind?: string
}): { primary: string; alternates: string[]; reason: string } | null {
  if (names.length === 0) return null
  // 1) role detection from member names
  const joined = names.join(' ')
  for (const [re, role] of VERB_ROLES) {
    if (re.test(joined)) {
      const suffix = names.length > 1 ? '流水线' : '组件'
      return {
        primary: `${role}${suffix}`,
        alternates: [`${role}服务`, `${role}核心`],
        reason: `成员名命中角色词（${re.source.slice(1, 24)}…）→ 职责驱动命名`,
      }
    }
  }
  // 2) shared stem
  const stem = sharedStem(names)
  if (stem && stem.length >= 3) {
    const cap = stem.split(' ').map((w) => w[0]?.toUpperCase() + w.slice(1)).join('')
    return {
      primary: cap,
      alternates: [`${cap}Pipeline`, `${cap}Service`],
      reason: `成员共享词干 "${stem}"`,
    }
  }
  // 3) port-driven (io names)
  const ports = extra?.ports ?? []
  if (ports.length >= 2) {
    const first = ports[0].name
    const second = ports[ports.length - 1].name
    return {
      primary: `${first}→${second}处理`,
      alternates: [`${first}${second}Pipeline`, '数据处理组件'],
      reason: '输入/输出端口名驱动的数据流命名',
    }
  }
  return {
    primary: names[0],
    alternates: [`${names[0]}Pipeline`, `${names[0]}Service`],
    reason: '仅单个成员：保留原名 + 常见后缀',
  }
}

// --- annotation -------------------------------------------------------------
export function suggestAnnotation(
  b: FlowBlock,
  ctx: { ports: FlowPort[]; nets: FlowNet[]; blocks: FlowBlock[] },
): { annotation: Record<string, string>; summary: string } {
  const ins = ctx.ports.filter((p) => p.direction === 'input')
  const outs = ctx.ports.filter((p) => p.direction === 'output')
  const resources = ctx.ports.filter((p) => p.semanticKind === 'resource')
  const inNets = ctx.nets.filter((n) => n.targetBlockId === b.id)
  const outNets = ctx.nets.filter((n) => n.sourceBlockId === b.id)
  const parents = ctx.nets
    .map((n) => n.sourceBlockId === b.id ? ctx.blocks.find((x) => x.id === n.targetBlockId)
      : n.targetBlockId === b.id ? ctx.blocks.find((x) => x.id === n.sourceBlockId) : null)
    .filter((x): x is FlowBlock => !!x)
  const ann: Record<string, string> = {
    displayName: b.name,
    description: `${KIND_LABEL[b.kind] ?? b.kind}「${b.name}」的职责说明。`,
    responsibility: `处理 ${ins.map((p) => p.name).join('、') || '输入'}；产出 ${
      outs.map((p) => p.name).join('、') || '输出'}${
      resources.length ? `；依赖资源：${resources.map((p) => p.name).join('、')}` : ''}`,
    notes: `连接数：上游 ${inNets.length} / 下游 ${outNets.length}${
      parents.length ? `；相邻组件：${parents.map((p) => p.name).join('、')}` : ''}`,
  }
  return {
    annotation: ann,
    summary: `基于 ${ins.length} 个输入 / ${outs.length} 个输出 / ${
      ctx.nets.length} 条连接生成`,
  }
}

// --- topology patch -----------------------------------------------------------
// §9 example: A → B with repeated recompute → 建议插入 ResultCache:
//   - Pipeline.result → Analyzer.input
//   + Pipeline.result → ResultCache.result
//   + ResultCache.cached_result → Analyzer.input
// Rule: when a data net's source block has ≥2 outgoing data nets (fan-out),
// suggest a cache in the middle.  Deterministic and safe (design-only).
export function suggestCacheInsert(
  net: FlowNet,
  ctx: { blocks: FlowBlock[]; ports: FlowPort[]; nets: FlowNet[] },
): AgentProposal | null {
  const ops: TopologyPatchOp[] = []
  const outData = ctx.nets.filter(
    (n) => n.sourceBlockId === net.sourceBlockId && n.kind !== 'control')
  if (outData.length < 2) return null
  const cacheName = 'ResultCache'
  const srcPort = ctx.ports.find((p) => p.id === net.sourcePortId)
  const tgt = ctx.blocks.find((b) => b.id === net.targetBlockId)
  if (!srcPort || !tgt) return null
  const srcBlock = ctx.blocks.find((b) => b.id === net.sourceBlockId)
  ops.push({ op: 'addBlock', block: { name: cacheName, kind: 'proposed', state: 'proposed' } })
  ops.push({
    op: 'addPort',
    port: { blockId: '__cache__', name: 'result', direction: 'input', semanticKind: srcPort.semanticKind },
  })
  ops.push({
    op: 'addPort',
    port: { blockId: '__cache__', name: 'cached_result', direction: 'output', semanticKind: srcPort.semanticKind },
  })
  ops.push({
    op: 'addPort',
    port: { blockId: '__cache__', name: 'CacheStore', direction: 'input', semanticKind: 'resource' },
  })
  ops.push({ op: 'removeNet', target: net.id })
  ops.push({ op: 'addNet', net: { sourcePortId: '__cacheout__', targetPortId: net.targetPortId, kind: net.kind } })
  ops.push({
    op: 'addNet',
    net: {
      sourcePortId: net.sourcePortId,
      targetPortId: '__cachein__',
      kind: srcPort.semanticKind as NetKind,
      meta: { guard: 'result is not None' },
    },
  })
  return {
    agentActionId: uuid(),
    intent: 'patch',
    prompt: '用缓存减少重复计算',
    summary: `在「${srcBlock?.name ?? '?'} → ${
      tgt.name}」之间插入 ${cacheName}，缓存已计算结果`,
    patch: {
      description: `${srcPort.name} → ${cacheName}.result；${cacheName}.cached_result → ${
        tgt.name}；资源端口 CacheStore`,
      ops,
    },
    affectedDesignIds: [net.sourceBlockId ?? '', net.targetBlockId ?? ''],
  }
}

/** §21 sketch: parse a free-text description into a Topology Proposal. */
const SKETCH_STEPS: Array<[RegExp, { name: string; role: 'data' | 'analysis' | 'cache' | 'notify' }]> = [
  [/采集|fetch|collect|ingest/i, { name: 'DataProvider', role: 'data' }],
  [/分析|analyz|compute|pipeline/i, { name: 'AnalysisPipeline', role: 'analysis' }],
  [/缓存|cache|缓存结果/i, { name: 'ResultCache', role: 'cache' }],
  [/通知|notify|推送|push|发送/i, { name: 'NotificationService', role: 'notify' }],
]

export function sketchToProposal(text: string): AgentProposal | null {
  const steps = SKETCH_STEPS.filter(([re]) => re.test(text))
  if (steps.length === 0) return null
  const ops: TopologyPatchOp[] = []
  for (const [, s] of steps) {
    ops.push({ op: 'addBlock', block: { name: s.name, kind: 'proposed', state: 'proposed' } })
  }
  // daisy-chain DATA nets between consecutive steps
  let prevName: string | null = null
  for (const [, s] of steps) {
    if (prevName) {
      ops.push({ op: 'addNet', net: { sourcePortId: '__x__', targetPortId: '__y__', kind: 'data', meta: { sketch: true } } })
    }
    prevName = s.name
  }
  return {
    agentActionId: uuid(),
    intent: 'sketch',
    prompt: text,
    summary: `从描述提取 ${steps.length} 个阶段：${steps
      .map(([, s]) => s.name)
      .join(' → ')}`,
    patch: { description: '草图：按阶段线性连接', ops },
    affectedDesignIds: [],
  }
}

// --- explain ------------------------------------------------------------------
export function explainTopology(
  ctx: { blocks: FlowBlock[]; ports: FlowPort[]; nets: FlowNet[] },
): string {
  const lines: string[] = []
  for (const b of ctx.blocks) {
    const ins = ctx.ports.filter((p) => p.blockId === b.id && p.direction === 'input')
    const outs = ctx.ports.filter((p) => p.blockId === b.id && p.direction === 'output')
    lines.push(
      `${b.name}（${KIND_LABEL[b.kind] ?? b.kind}）: 输入 ${
        ins.map((p) => p.name).join('、') || '无'} → 输出 ${outs.map((p) => p.name).join('、') || '无'}`)
  }
  for (const n of ctx.nets) {
    const s = ctx.blocks.find((b) => b.id === n.sourceBlockId)
    const t = ctx.blocks.find((b) => b.id === n.targetBlockId)
    if (s && t) lines.push(`  ${s.name} ──${n.kind}──> ${t.name}`)
  }
  return lines.join('\n')
}

export const AGENT_INTENTS = {
  name: 'naming',
  describe: 'annotation',
  patch: 'patch',
  sketch: 'sketch',
  explain: 'explain',
} as const
