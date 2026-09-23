import { defineStore } from 'pinia'
import {
  applyDesignCommand, getDesignChange, listDesignChanges, openDesignChange,
  type OpenDesignChange,
} from '../api/design-lifecycle'
import type { DesignChange, DesignCommand } from '../domain/design-lifecycle'

// Human UI adapter: the backend aggregate is the source of truth.  The store
// intentionally has no local lifecycle reducer or alternate state machine.
export const useDesignLifecycleStore = defineStore('designLifecycle', {
  state: () => ({
    changes: [] as DesignChange[],
    current: null as DesignChange | null,
    loading: false,
  }),
  actions: {
    async list(repoId?: string): Promise<void> {
      this.loading = true
      try { this.changes = (await listDesignChanges(repoId)).changes }
      finally { this.loading = false }
    },
    async get(changeId: string): Promise<DesignChange> {
      this.current = await getDesignChange(changeId)
      return this.current
    },
    async open(input: OpenDesignChange): Promise<DesignChange> {
      this.current = await openDesignChange(input)
      this.changes = [this.current, ...this.changes.filter((x) => x.id !== this.current?.id)]
      return this.current
    },
    async command(changeId: string, command: DesignCommand): Promise<DesignChange> {
      this.current = await applyDesignCommand(changeId, command)
      this.changes = this.changes.map((x) => x.id === changeId ? this.current! : x)
      return this.current
    },
  },
})
