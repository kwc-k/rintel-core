<script setup lang="ts">
import { useToastStore } from '../stores/toast'

const toast = useToastStore()
</script>

<template>
  <div v-if="toast.offline" class="offline-banner" role="alert">
    <span class="dot" aria-hidden="true"></span>
    <span>后端不可用（离线）— 显示缓存内容，重试连接中…</span>
    <button class="ghost" type="button" @click="toast.setOffline(false)">忽略</button>
  </div>
</template>

<style scoped>
.offline-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #7f1d1d;
  color: #fff;
  padding: 6px 12px;
  font-size: 12px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #fca5a5;
  flex-shrink: 0;
}
.ghost {
  margin-left: auto;
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.4);
  color: #fff;
}
.ghost:hover {
  background: rgba(255, 255, 255, 0.15);
}
</style>
