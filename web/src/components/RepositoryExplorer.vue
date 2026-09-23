<script setup lang="ts">
import { useWorkspaceStore } from '../stores/workspace'
import TreeBrowser from './TreeBrowser.vue'
import MultiSelectBar from './MultiSelectBar.vue'

const workspace = useWorkspaceStore()
</script>

<template>
  <div class="panel explorer">
    <div class="panel-header">Repository Explorer</div>
    <div class="panel-body">
      <MultiSelectBar />
      <TreeBrowser v-if="workspace.repoId" :repo-id="workspace.repoId" />
      <div v-else class="empty">
        <div class="empty-title">添加 repository</div>
        <div>使用顶部「添加 repository」打开一个代码库。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.explorer {
  height: 100%;
}
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
