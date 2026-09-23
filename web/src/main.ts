import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { useThemeStore } from './stores/theme'
import { useLangStore } from './stores/lang'
import { setupI18n } from './lib/lang'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(setupI18n())
// THEME0: apply the stored/system appearance before first paint
// (no theme flash; components read CSS variables afterwards).
useThemeStore().init()
// TOPO-EDITOR-UX0 §13: language before first paint (default 中文 / stored).
useLangStore().init()
app.use(router)
app.mount('#app')
