<script setup lang="ts">
// WORKBENCH §6: Repository Explorer for the topology context — module /
// file / function tree of the active analysis lane, search filter,
// refresh, locate-current-symbol, and Monaco drill-down.  Read-only view
// of the frozen lane bundle (never mutates canonical topology).
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTopoStore } from '../../stores/topo'
import type { ContainerRef } from '../../domain/topo'

const { t } = useI18n()
const store = useTopoStore()

const query = ref('')
const openModules = ref(new Set<string>())   // module:name | sugg:i
const openFiles = ref(new Set<string>())     // file path

const index = computed(() => store.index)
const bundle = computed(() => store.bundle)

interface FnRow {
  id: string
  name: string
  file?: string | null
  language?: string
  crossCutting: boolean
}
interface FileRow {
  id: string
  path: string
  label: string
  fns: FnRow[]
}
interface ModRow {
  id: string
  label: string
  sub: string
  files: FileRow[]
  suggestion?: boolean
}
interface SuggRow {
  idx: number
  id: string
  label: string
  sub: string
  files: FileRow[]
}

const rows = computed(() => {
  const idx = index.value
  const b = bundle.value
  if (!idx || !b) return { mods: [] as ModRow[], suggs: [] as SuggRow[] }
  const q = query.value.trim().toLowerCase()
  const mods: ModRow[] = []
  for (const mod of b.declared_modules) {
    const files: FileRow[] = []
    for (const f of idx.filesByModule.get(mod) ?? []) {
      const fns = (idx.fnIdsByFile.get(f) ?? []).map((cid) => ({
        id: cid,
        name: idx.nodeById.get(cid)?.name ?? cid,
        file: f,
        language: idx.nodeById.get(cid)?.language,
        crossCutting: idx.cc.has(cid),
      }))
      files.push({ id: `file:${f}`, path: f, label: f.split('/').slice(-1)[0], fns })
    }
    const m: ModRow = {
      id: `module:${mod}`,
      label: mod,
      sub: `${idx.fnIdsByModule.get(mod)?.length ?? 0} ${t('wb.node.fns')} · ${files.length} ${t('wb.node.files')}`,
      files,
    }
    if (!q || m.label.toLowerCase().includes(q) || files.some((f) =>
      f.path.toLowerCase().includes(q) || f.fns.some((fn) => fn.name.toLowerCase().includes(q)))) {
      mods.push(m)
    }
  }
  const suggs: SuggRow[] = []
  b.suggestions.forEach((s, i) => {
    const files: FileRow[] = []
    for (const f of idx.suggFiles.get(i) ?? []) {
      const fns = s.members
        .filter((m) => idx.fileOf.get(m) === f)
        .map((cid) => ({ id: cid, name: idx.nodeById.get(cid)?.name ?? cid, file: f, language: idx.nodeById.get(cid)?.language, crossCutting: idx.cc.has(cid) }))
      files.push({ id: `file:${f}`, path: f, label: f.split('/').slice(-1)[0], fns })
    }
    suggs.push({
      idx: i,
      id: `sugg:${i}`,
      label: s.classification?.declared_module ?? s.members[0]?.split(':').pop() ?? `Suggested ${i + 1}`,
      sub: `${s.members.length} ${t('wb.node.fns')} · ${files.length} ${t('wb.node.files')}`,
      files,
    })
  })
  return { mods, suggs }
})

function toggleMod(id: string): void {
  const next = new Set(openModules.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openModules.value = next
}
function toggleFile(id: string): void {
  const next = new Set(openFiles.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openFiles.value = next
}

function selectModule(id: string, label: string): void {
  store.selectNode(id)
  store.popTo(0)
}
function drillModule(id: string, label: string): void {
  store.popTo(0)
  store.push({ kind: 'module', id: label, label })
  store.selectNode(null)
}
function drillFile(path: string, label: string): void {
  // go to module level first, then push the file (function level)
  const mod = index.value?.filesByModule ? moduleOfFile(path) : null
  if (mod) {
    store.popTo(0)
    store.push({ kind: 'module', id: mod, label: mod })
  }
  store.push({ kind: 'file', id: path, label })
  store.selectNode(null)
}
function moduleOfFile(path: string): string | null {
  const idx = index.value
  if (!idx) return null
  for (const [mod, files] of idx.filesByModule) {
    if (files.includes(path)) return mod
  }
  return null
}
function selectFn(id: string): void {
  store.selectNode(id)
}
function openFnSource(f: FnRow): void {
  store.openSource(bundle.value?.meta.source_repo_id ?? null, f.file ?? f.name, null)
}

function locate(): void {
  const sel = store.selection
  if (!sel || sel.type !== 'node') return
  const id = sel.id
  if (id.startsWith('module:')) {
    store.popTo(0)
    store.selectNode(id)
    return
  }
  if (id.startsWith('file:')) {
    const path = id.slice('file:'.length)
    drillFile(path, path.split('/').slice(-1)[0])
    return
  }
  if (id.startsWith('sugg:')) {
    store.popTo(0)
    store.selectNode(id)
    return
  }
  const idx = index.value
  const mod = idx?.modOfFn.get(id)
  const file = idx?.fileOf.get(id)
  if (mod && file) {
    store.popTo(0)
    store.push({ kind: 'module', id: mod, label: mod })
    store.push({ kind: 'file', id: file, label: file.split('/').slice(-1)[0] })
    store.selectNode(id)
  } else {
    store.popTo(0)
    store.selectNode(id)
  }
}

async function refresh(): Promise<void> {
  if (store.lane) await store.loadLane(store.lane)
}

const isActive = (id: string): boolean => store.selection?.type === 'node' && store.selection.id === id

function openSourceOfFile(f: FileRow): void {
  const s = f.fns[0]
  if (s) openFnSource(s)
}
</script>

<template>
  <div class="wtree" data-testid="topology-tree">
    <div class="wtree-tools">
      <input
        v-model="query" class="wtree-search" :placeholder="t('wb.explorer.search')"
        data-testid="explorer-search"
      />
      <button type="button" class="wtool" :title="t('wb.explorer.locate')" data-testid="explorer-locate" @click="locate">
        ⌖
      </button>
      <button type="button" class="wtool" :title="t('wb.explorer.refresh')" data-testid="explorer-refresh" @click="refresh">
        ⟳
      </button>
      <button type="button" class="wtool" :title="t('wb.explorer.backTop')" data-testid="explorer-top" @click="store.popTo(0)">
        ↑
      </button>
    </div>

    <div v-if="store.lane" class="wtree-lane mono" data-testid="explorer-lane">{{ store.lane }}</div>
    <div v-if="!store.bundle" class="wtree-empty">{{ t('wb.explorer.emptyLane') }}</div>

    <div v-else class="wtree-body">
      <div v-for="m in rows.mods" :key="m.id" class="wb-row mod">
        <div class="wb-row-head" :class="{ active: isActive(m.id) }" data-testid="explorer-module"
             @click="selectModule(m.id, m.label)" @dblclick="drillModule(m.id, m.label)">
          <button type="button" class="chev wb-chev" :class="{ open: openModules.has(m.id) }" @click.stop="toggleMod(m.id)">▸</button>
          <span class="wb-kind kind-module">{{ t('wb.tree.kindMod') }}</span>
          <span class="wb-name">{{ m.label }}</span>
          <span class="wb-sub">{{ m.sub }}</span>
        </div>
        <div v-if="openModules.has(m.id)" class="wb-children">
          <div v-for="f in m.files" :key="f.id" class="wb-row file">
            <div class="wb-row-head" :class="{ active: isActive(f.id) }" data-testid="explorer-file"
                 @click="drillFile(f.path, f.label)" @dblclick="openSourceOfFile(f)">
              <button type="button" class="chev wb-chev" :class="{ open: openFiles.has(f.id) }" @click.stop="toggleFile(f.id)">▸</button>
              <span class="wb-kind kind-file">{{ t('wb.tree.kindFile') }}</span>
              <span class="wb-name">{{ f.label }}</span>
              <span class="wb-sub">{{ f.fns.length }}</span>
            </div>
            <div v-if="openFiles.has(f.id)" class="wb-children">
              <div
                v-for="fn in f.fns" :key="fn.id"
                class="wb-row fn"
                :class="{ 'xcut': fn.crossCutting, active: isActive(fn.id) }"
                data-testid="explorer-function"
                @click="selectFn(fn.id)"
                @dblclick="openFnSource(fn)"
              >
                <span class="wb-kind kind-fn">{{ t('wb.tree.kindFn') }}</span>
                <span class="wb-name">{{ fn.name }}</span>
                <span v-if="fn.crossCutting" class="wb-tag">✂</span>
                <button type="button" class="wb-open" :title="fn.file ?? ''" @click.stop="openFnSource(fn)">⎘</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-for="s in rows.suggs" :key="s.id" class="wb-row sugg">
        <div class="wb-row-head" :class="{ active: isActive(s.id) }" @click="selectModule(s.id, s.label)"
             @dblclick="drillModule(s.id, s.label)">
          <button type="button" class="chev wb-chev" :class="{ open: openModules.has(s.id) }" @click.stop="toggleMod(s.id)">▸</button>
          <span class="wb-kind kind-sugg">{{ t('wb.tree.kindSugg') }}</span>
          <span class="wb-name">{{ s.label }}</span>
          <span class="wb-sub">{{ s.sub }}</span>
        </div>
        <div v-if="openModules.has(s.id)" class="wb-children">
          <div v-for="f in s.files" :key="f.id" class="wb-row file">
            <div class="wb-row-head" :class="{ active: isActive(f.id) }" @click="drillFile(f.path, f.label)"
                 @dblclick="openSourceOfFile(f)">
              <button type="button" class="chev wb-chev" :class="{ open: openFiles.has(f.id) }" @click.stop="toggleFile(f.id)">▸</button>
              <span class="wb-kind kind-file">{{ t('wb.tree.kindFile') }}</span>
              <span class="wb-name">{{ f.label }}</span>
              <span class="wb-sub">{{ f.fns.length }}</span>
            </div>
            <div v-if="openFiles.has(f.id)" class="wb-children">
              <div v-for="fn in f.fns" :key="fn.id" class="wb-row fn" :class="{ active: isActive(fn.id) }"
                   @click="selectFn(fn.id)" @dblclick="openFnSource(fn)">
                <span class="wb-kind kind-fn">{{ t('wb.tree.kindFn') }}</span>
                <span class="wb-name">{{ fn.name }}</span>
                <button type="button" class="wb-open" :title="fn.file ?? ''" @click.stop="openFnSource(fn)">⎘</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wtree {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  font-size: 11.5px;
}
.wtree-tools {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
}
.wtree-search {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 6px;
  font-size: 11px;
  background: var(--surface);
  color: var(--text-primary);
}
.wtool {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  border-radius: 4px;
  padding: 2px 6px;
  cursor: pointer;
  font-size: 11px;
  flex-shrink: 0;
}
.wtool:hover { color: var(--accent); border-color: var(--accent); }
.wtree-lane {
  padding: 3px 10px;
  font-size: 10px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: var(--panel);
}
.wtree-empty { padding: 16px 10px; color: var(--text-muted); font-size: 11px; }
.wtree-body { flex: 1; overflow: auto; padding: 4px 4px 12px; }
.wb-row-head, .wb-row.fn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  border-radius: 3px;
  cursor: pointer;
  color: var(--text-primary);
}
.wb-row-head:hover, .wb-row.fn:hover { background: var(--hover); }
.wb-row-head.active, .wb-row.fn.active { background: var(--sel-bg); color: var(--sel-text); }
.wb-chev {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 9px;
  padding: 0;
  width: 12px;
  flex-shrink: 0;
  cursor: pointer;
}
.wb-chev.open { transform: rotate(90deg); }
.wb-kind {
  flex-shrink: 0;
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.4px;
  padding: 0 3px;
  border-radius: 3px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  background: var(--surface);
}
.kind-module { color: var(--topo-control); border-color: var(--topo-control); }
.kind-file { color: var(--topo-call); border-color: var(--topo-call); }
.kind-fn { color: var(--topo-state); border-color: var(--topo-state); }
.kind-sugg { color: var(--status-warning); border-color: var(--status-warning); }
.wb-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 1;
  min-width: 0;
}
.wb-sub { color: var(--text-muted); font-size: 10px; margin-left: auto; flex-shrink: 0; }
.wb-row.fn { padding-left: 34px; }
.wb-tag { color: var(--status-warning); font-size: 9px; }
.wb-open {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 11px;
  flex-shrink: 0;
  opacity: 0;
}
.wb-row.fn:hover .wb-open { opacity: 1; }
.wb-open:hover { color: var(--accent); }
.wb-row.fn.xcut .wb-name { color: var(--status-warning); }
.wb-children { padding-left: 12px; }
</style>
