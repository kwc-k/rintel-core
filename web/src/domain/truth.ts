// UI-REALITY-ALIGN0 §10-§13: ONE truth vocabulary for the whole workbench.
//
// The rules this file encodes (they are product rules, not styling choices):
//   * UNKNOWN ≠ FALSE            — "not observed" is not "does not exist"
//   * PARTIAL ≠ COMPLETE         — partial coverage must never read as full
//   * HEURISTIC ≠ Evidence       — a derived/rule-based grouping is an annotation
//   * DESIGN  ≠ Evidence         — a user-drawn TO-BE edge is not a canonical CALL
//   * PROBE SITE ≠ full coverage — selective instrumentation is labelled
// Every badge renders text + glyph + tooltip; colour is only an aid.

export type TruthClass =
  | 'OBSERVED' | 'RESOLVED' | 'DERIVED' | 'INFERRED' | 'HEURISTIC' | 'UNKNOWN'
export type Coverage = 'COMPLETE' | 'PARTIAL' | 'UNKNOWN' | 'MIXED'
export type SpanPrecision = 'EXACT' | 'LINE_ONLY' | 'PARTIAL' | 'UNKNOWN'
export type Plane = 'STATIC' | 'OBSERVED' | 'DESIGN' | 'ANNOTATION' | 'PROBE' | 'PROPOSAL'

export interface TruthInfo { glyph: string; zh: string; en: string; why: string; tone: string }
export interface CoverageInfo { glyph: string; zh: string; en: string; why: string; tone: string }
export interface PlaneInfo { glyph: string; zh: string; en: string; dash: string; why: string }

export const TRUTH: Record<TruthClass, TruthInfo> = {
  OBSERVED: { glyph: '◉', zh: '已观测', en: 'OBSERVED', tone: 'observed',
    why: '本次运行真实记录（runtime trace / data event），不是推断' },
  RESOLVED: { glyph: '✓', zh: '已解析', en: 'RESOLVED', tone: 'resolved',
    why: '解析器把符号/目标绑定到唯一实体' },
  DERIVED: { glyph: '∑', zh: '派生', en: 'DERIVED', tone: 'derived',
    why: '由已冻结的产物推导出来（例如端口/形状），不是源码原文' },
  INFERRED: { glyph: '≈', zh: '推断', en: 'INFERRED', tone: 'derived',
    why: '跨文件推断，可能有例外' },
  HEURISTIC: { glyph: '⚑', zh: '启发式', en: 'HEURISTIC', tone: 'heuristic',
    why: '规则/聚类产生，属于标注平面，不是证据' },
  UNKNOWN: { glyph: '?', zh: '未知', en: 'UNKNOWN', tone: 'unknown',
    why: '没有足够证据 —— 未知不等于不存在' },
}

export const COVERAGE: Record<Coverage, CoverageInfo> = {
  COMPLETE: { glyph: '●', zh: '完整', en: 'COMPLETE', tone: 'complete',
    why: '该范围内证据齐全（逐条来自冻结产物统计）' },
  PARTIAL: { glyph: '◐', zh: '部分', en: 'PARTIAL', tone: 'partial',
    why: '只覆盖一部分：选择性探针/部分建模，不能当作全集' },
  UNKNOWN: { glyph: '○', zh: '未知覆盖', en: 'UNKNOWN', tone: 'unknown',
    why: '尚无证据判断覆盖度 —— 不是"没有发生"' },
  MIXED: { glyph: '◑', zh: '混合', en: 'MIXED', tone: 'partial',
    why: '同一范围内不同成员覆盖不同' },
}

export const PLANES: Record<Plane, PlaneInfo> = {
  STATIC: { glyph: '│', zh: '静态', en: 'STATIC', dash: 'none',
    why: '来自源码索引（未运行）—— 可能发生' },
  OBSERVED: { glyph: '◉', zh: '已观测', en: 'OBSERVED', dash: 'none',
    why: '来自一次具体运行 —— 确实发生' },
  DESIGN: { glyph: '✎', zh: '设计 TO-BE', en: 'DESIGN (TO-BE)', dash: '6 4',
    why: '用户绘制的目标态 —— 不是 canonical evidence' },
  ANNOTATION: { glyph: '⚑', zh: '人工语义标注', en: 'HUMAN SEMANTIC', dash: '2 4',
    why: 'Human Semantic 标注平面，成员来自 canonical support，标签本身是标注' },
  PROBE: { glyph: '◌', zh: '选择性探针', en: 'PROBE SITE', dash: '3 3',
    why: '在该点插桩得到的观测 —— 覆盖是选择性的' },
  PROPOSAL: { glyph: '?', zh: '提案', en: 'PROPOSAL', dash: '5 5',
    why: 'Agent/建议产物，必须显式接受才成为设计' },
}

export const PRECISION: Record<SpanPrecision, { zh: string; en: string; why: string }> = {
  EXACT: { zh: '精确范围', en: 'EXACT', why: '行列精确，可直接高亮该范围' },
  LINE_ONLY: { zh: '仅行', en: 'LINE_ONLY', why: '只有行号，没有列 —— 不会伪造列' },
  PARTIAL: { zh: '部分范围', en: 'PARTIAL', why: '范围不完整（例如多行表达式只锚定一行）' },
  UNKNOWN: { zh: '无位置', en: 'UNKNOWN', why: '没有记录位置信息' },
}

export function truthInfo(t?: string | null): TruthInfo {
  return TRUTH[(t ?? 'UNKNOWN').toUpperCase() as TruthClass] ?? TRUTH.UNKNOWN
}
export function coverageInfo(c?: string | null): CoverageInfo {
  return COVERAGE[(c ?? 'UNKNOWN').toUpperCase() as Coverage] ?? COVERAGE.UNKNOWN
}
export function planeInfo(p?: string | null): PlaneInfo {
  return PLANES[(p ?? 'STATIC').toUpperCase() as Plane] ?? PLANES.STATIC
}
export function precisionInfo(p?: string | null) {
  return PRECISION[(p ?? 'UNKNOWN').toUpperCase() as SpanPrecision] ?? PRECISION.UNKNOWN
}

/** Shape display: `[15, 15]` — one canonical rendering everywhere, so a
 *  symbolic dim (`n`) and a number never look different by accident. */
export function formatShape(shape?: unknown[] | null): string {
  if (!shape || !shape.length) return ''
  return `[${shape.map((d) => (d === null || d === undefined ? '?' : String(d))).join(', ')}]`
}

/** SourceSpan display: `file:line:col-end:col` when EXACT, `file:line` when
 *  LINE_ONLY — never a fake `:0`. */
export function spanLabel(span?: Record<string, any> | null): string {
  if (!span) return ''
  if (span.text) return String(span.text)
  const { file, start_line, start_column, end_line, end_column } = span
  if (!file || !start_line) return String(file ?? '')
  if (start_column && end_column) {
    return start_line === end_line
      ? `${file}:${start_line}:${start_column}-${end_column}`
      : `${file}:${start_line}:${start_column}-${end_line}:${end_column}`
  }
  return end_line && end_line !== start_line ? `${file}:${start_line}-${end_line}` : `${file}:${start_line}`
}

/** §11/§29: how a call edge reads in words, from ITS OWN facts. */
export function edgePlaneLabel(staticPresent: boolean, observedCount?: number | null): string {
  if (staticPresent && observedCount) return 'STATIC + OBSERVED'
  if (staticPresent && !observedCount) return 'STATIC · NOT OBSERVED THIS RUN'
  if (!staticPresent && observedCount) return 'OBSERVED ONLY (not in static graph)'
  return 'UNKNOWN'
}
