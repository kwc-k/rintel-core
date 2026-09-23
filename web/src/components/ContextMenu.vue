<script setup lang="ts">
// TOPO-EDITOR-UX0 §2-§4/§24: IDE-style context menu (compact, grouped,
// submenus, shortcut hints).  Teleported to body; closes on click-outside
// / Esc.  Populated declaratively by the caller, no hardcoded theme colors.
import { onBeforeUnmount, onMounted, ref, computed } from 'vue'
import { useLangStore } from '../stores/lang'

export interface MenuItem {
  id: string
  label: string
  hint?: string                 // shortcut / kbd hint
  danger?: boolean
  disabled?: boolean
  action?: () => void
  children?: MenuItem[]
  separator?: boolean           // render as group separator
  testid?: string
}

const props = defineProps<{ x: number; y: number; items: MenuItem[] }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const lang = useLangStore()
const openSub = ref<string | null>(null)

const style = computed(() => ({
  left: `${Math.min(props.x, window.innerWidth - 240)}px`,
  top: `${Math.min(props.y, window.innerHeight - 40)}px`,
}))

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('close')
}

function click(item: MenuItem): void {
  if (item.disabled) return
  if (item.children) { openSub.value = openSub.value === item.id ? null : item.id; return }
  item.action?.()
  emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('mousedown', onGlobalDown, true)
  window.addEventListener('contextmenu', onGlobalCtx, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('mousedown', onGlobalDown, true)
  window.removeEventListener('contextmenu', onGlobalCtx, true)
})

let menuEl: HTMLElement | null = null
function setEl(el: unknown): void { menuEl = el as HTMLElement | null }
function onGlobalDown(e: MouseEvent): void {
  if (menuEl && !menuEl.contains(e.target as Node)) emit('close')
}
// §7: a right-click elsewhere REPLACES this menu — the callers open the next
// one in their own contextmenu handler; closing here first guarantees a
// fresh menu even if mousedown is suppressed by overlays.
function onGlobalCtx(e: MouseEvent): void {
  if (menuEl && !menuEl.contains(e.target as Node)) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div ref="setEl" class="ctx-menu" :style="style" data-testid="ctx-menu" role="menu">
      <template v-for="item in items" :key="item.id">
        <div v-if="item.separator" class="ctx-sep" role="separator"></div>
        <div
          v-else
          class="ctx-item"
          :class="{ danger: item.danger, disabled: item.disabled }"
          :data-testid="item.testid ?? `ctx-${item.id}`"
          role="menuitem"
          @click="click(item)"
          @mouseenter="openSub = null"
        >
          <span class="ctx-label">{{ item.label }}</span>
          <span v-if="item.hint" class="ctx-hint">{{ item.hint }}</span>
          <span v-if="item.children" class="ctx-arrow">›</span>
          <div v-if="item.children && openSub === item.id" class="ctx-menu ctx-sub" role="menu">
            <template v-for="sub in item.children" :key="sub.id">
              <div v-if="sub.separator" class="ctx-sep"></div>
              <div
                v-else class="ctx-item"
                :class="{ danger: sub.danger, disabled: sub.disabled }"
                :data-testid="sub.testid ?? `ctx-${sub.id}`"
                role="menuitem"
                @click="click(sub)"
              >
                <span class="ctx-label">{{ sub.label }}</span>
                <span v-if="sub.hint" class="ctx-hint">{{ sub.hint }}</span>
              </div>
            </template>
          </div>
        </div>
      </template>
    </div>
  </Teleport>
</template>

<style>
/* non-scoped: teleported to body, so scoped attributes would not match */
.ctx-menu {
  position: fixed;
  z-index: 1000;
  min-width: 190px;
  max-width: 260px;
  background: var(--panel-bg);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  padding: 4px;
  font-size: 12px;
  color: var(--text-primary);
  user-select: none;
}
.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  position: relative;
  white-space: nowrap;
}
.ctx-item:hover { background: var(--hover); }
.ctx-item.danger { color: var(--status-error); }
.ctx-item.disabled { opacity: 0.45; cursor: default; }
.ctx-label { flex: 1; }
.ctx-hint { font-size: 10px; color: var(--text-secondary); font-family: var(--mono); }
.ctx-arrow { color: var(--text-secondary); }
.ctx-sep { height: 1px; background: var(--border); margin: 3px 6px; }
.ctx-sub {
  position: absolute;
  left: calc(100% - 2px);
  top: -5px;
}
</style>
