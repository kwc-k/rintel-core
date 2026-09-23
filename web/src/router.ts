import { createRouter, createWebHistory } from 'vue-router'
import WorkbenchLayout from './components/WorkbenchLayout.vue'
import WorkbenchView from './views/WorkbenchView.vue'
import ChangeWorkspaceView from './views/ChangeWorkspaceView.vue'
import ChangeListView from './views/ChangeListView.vue'

// Business routes (WORKBENCH §3):
//   /            legacy repository workbench (S2/S3 evidence + architecture)
//   /topology    Software EDA workbench shell — topology tab (evidence plane)
//   /flows/:id   the same shell on the circuit tab (Design TO-BE plane)
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'workbench', component: WorkbenchLayout },
    { path: '/topology', name: 'workbench-topology', component: WorkbenchView },
    { path: '/flows/:id', name: 'workbench-flow', component: WorkbenchView },
    { path: '/changes', name: 'change-list', component: ChangeListView },
    { path: '/changes/:id', name: 'change-workspace', component: ChangeWorkspaceView },
  ],
})
