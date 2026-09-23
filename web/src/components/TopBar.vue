<script setup lang="ts">
import { useRouter } from 'vue-router'
import ThemeSwitch from './ThemeSwitch.vue'
import LangSwitch from './LangSwitch.vue'
import RepoSwitcher from './RepoSwitcher.vue'
import SnapshotBadge from './SnapshotBadge.vue'
import IndexButton from './IndexButton.vue'
import IndexProgress from './IndexProgress.vue'
import SearchPalette from './SearchPalette.vue'
import { version } from '../../package.json'

const router = useRouter()
</script>

<template>
  <header class="topbar">
    <div class="left">
      <span class="brand">rintel <small>v{{ version }}</small></span>
      <RepoSwitcher />
      <SnapshotBadge />
      <IndexButton />
      <IndexProgress />
      <button type="button" class="nav-link" @click="router.push('/topology')">
        Software Topology
      </button>
      <button type="button" class="nav-link" @click="router.push('/changes')">
        Design Changes
      </button>
    </div>
    <div class="right">
      <LangSwitch />
      <ThemeSwitch />
      <SearchPalette />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.left,
.right {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.brand {
  font-weight: 700;
  letter-spacing: -0.02em;
  font-size: 15px;
  flex-shrink: 0;
}
.nav-link {
  font-size: 12px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  background: var(--panel);
  padding: 3px 8px;
  cursor: pointer;
  flex-shrink: 0;
}
</style>
