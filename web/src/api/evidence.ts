// Typed evidence-plane API (S2-WEB-CONTRACT §2). All responses are mapped
// from snake_case DTOs to camelCase domain models in `src/domain/evidence.ts`.

import { apiGet, apiPost, qs } from './client'
import type {
  JobInfo,
  NeighborhoodResult,
  RawNeighborhood,
  RawSearchItem,
  RawStructuralOverview,
  RepoInfo,
  SearchResultItem,
  SnapshotInfo,
  SourceResponse,
  StructuralOverview,
  TreeItem,
  EvidenceRecord,
  RawEvidenceRecord,
  RawTreeItem,
} from '../domain/evidence'
import { mapNeighborhood, mapStructuralOverview, mapSearchItem, mapEvidenceRecord, mapTreeItem } from '../domain/evidence'

export interface ReposResponse {
  items: RepoInfo[]
  total: number
}

export interface CreateRepoResponse {
  repo_id: string
  job_id: string
}

export interface SnapshotsResponse {
  items: SnapshotInfo[]
  total: number
}

export interface TreeResponse {
  items: TreeItem[]
  total: number
}

export interface SymbolsResponse {
  items: SymbolItem[]
  total: number
}

export interface SymbolItem {
  id: string
  kind: string
  name: string
  qname: string
  language: string
  path: string
  line?: number
  startLine?: number
  endLine?: number
}

export interface SearchResponse {
  items: SearchResultItem[]
  total: number
  limit: number
  snapshot: string
}

export interface EvidenceResponse {
  items: EvidenceRecord[]
  total: number
}

interface ListParams {
  offset?: number
  limit?: number
  snapshot?: string
}

interface RawRepo {
  id: string
  root_path: string
  created_at: number
  latest_snapshot: string | null
  node_count: number
  edge_count: number
}

export const evidenceApi = {
  async listRepos(): Promise<ReposResponse> {
    const raw = await apiGet<{ items: RawRepo[]; total: number }>('/repos')
    return {
      items: (raw.items ?? []).map((r) => ({
        id: r.id,
        rootPath: r.root_path,
        createdAt: r.created_at,
        latestSnapshot: r.latest_snapshot,
        nodeCount: r.node_count,
        edgeCount: r.edge_count,
      })),
      total: raw.total,
    }
  },

  createRepo(rootPath: string, label?: string): Promise<CreateRepoResponse> {
    return apiPost<CreateRepoResponse>('/repos', { root_path: rootPath, label })
  },

  /** Native folder picker on the server host (macOS/Linux/Windows).
   *  Returns {path} or {path: null} when cancelled / unavailable. */
  pickRepoFolder(): Promise<{ path: string | null }> {
    return apiPost<{ path: string | null }>('/repos/pick-folder')
  },

  triggerIndex(repoId: string, force = false): Promise<{ job_id: string }> {
    return apiPost<{ job_id: string }>(`/repos/${encodeURIComponent(repoId)}/index${qs({ force: force || undefined })}`)
  },

  listSnapshots(repoId: string): Promise<SnapshotsResponse> {
    return apiGet<SnapshotsResponse>(`/repos/${encodeURIComponent(repoId)}/snapshots`)
  },

  async getTree(repoId: string, opts: { path?: string; q?: string } & ListParams): Promise<TreeResponse> {
    const raw = await apiGet<{ items: RawTreeItem[]; total: number }>(
      `/repos/${encodeURIComponent(repoId)}/tree${qs({ path: opts.path ?? '', q: opts.q, snapshot: opts.snapshot, offset: opts.offset, limit: opts.limit })}`,
    )
    return { items: (raw.items ?? []).map(mapTreeItem), total: raw.total }
  },

  async getSymbols(repoId: string, path: string, opts: ListParams = {}): Promise<SymbolsResponse> {
    const raw = await apiGet<{
      items: { id: string; kind: string; name: string; qname: string; language: string; path: string; line?: number; start_line?: number; end_line?: number }[]
      total: number
    }>(
      `/repos/${encodeURIComponent(repoId)}/symbols${qs({ path, snapshot: opts.snapshot, offset: opts.offset, limit: opts.limit })}`,
    )
    return {
      items: (raw.items ?? []).map((s) => ({
        id: s.id,
        kind: s.kind,
        name: s.name,
        qname: s.qname,
        language: s.language,
        path: s.path,
        line: s.line,
        startLine: s.start_line,
        endLine: s.end_line,
      })),
      total: raw.total,
    }
  },

  async getStructuralOverview(
    repoId: string,
    opts: { snapshot?: string; depth?: number; nodeBudget?: number; edgeBudget?: number } = {},
  ): Promise<StructuralOverview> {
    const raw = await apiGet<RawStructuralOverview>(
      `/repos/${encodeURIComponent(repoId)}/structural-overview${qs({
        snapshot: opts.snapshot,
        depth: opts.depth,
        node_budget: opts.nodeBudget,
        edge_budget: opts.edgeBudget,
      })}`,
    )
    return mapStructuralOverview(raw)
  },

  async search(
    q: string,
    repoId: string,
    opts: { snapshot?: string; limit?: number; kinds?: string } = {},
  ): Promise<SearchResponse> {
    const raw = await apiGet<{ items: RawSearchItem[]; total: number; limit: number; snapshot: string }>(
      `/search${qs({ q, repo_id: repoId, snapshot: opts.snapshot, limit: opts.limit, kinds: opts.kinds })}`,
    )
    return {
      items: (raw.items ?? []).map(mapSearchItem),
      total: raw.total,
      limit: raw.limit,
      snapshot: raw.snapshot,
    }
  },

  async getNeighborhood(
    repoId: string,
    node: string,
    opts: { snapshot?: string; depth?: number; nodeBudget?: number; edgeBudget?: number; direction?: 'both' | 'in' | 'out' } = {},
  ): Promise<NeighborhoodResult> {
    const raw = await apiGet<RawNeighborhood>(
      `/graph/neighborhood${qs({
        repo_id: repoId,
        node,
        snapshot: opts.snapshot,
        depth: opts.depth,
        node_budget: opts.nodeBudget,
        edge_budget: opts.edgeBudget,
        direction: opts.direction,
      })}`,
    )
    return mapNeighborhood(raw)
  },

  async getEvidence(entityType: 'node' | 'edge', entityId: string, repoId: string, snapshot?: string): Promise<EvidenceResponse> {
    const raw = await apiGet<{ items: RawEvidenceRecord[]; total: number }>(
      `/evidence${qs({ entity_type: entityType, entity_id: entityId, repo_id: repoId, snapshot })}`,
    )
    return { items: (raw.items ?? []).map(mapEvidenceRecord), total: raw.total }
  },

  getSource(
    repoId: string,
    path: string,
    opts: { snapshot?: string; start?: number; end?: number } = {},
  ): Promise<SourceResponse> {
    return apiGet<SourceResponse>(
      `/source${qs({ repo_id: repoId, path, snapshot: opts.snapshot, start: opts.start, end: opts.end })}`,
    )
  },

  getJob(jobId: string): Promise<JobInfo> {
    return apiGet<JobInfo>(`/jobs/${encodeURIComponent(jobId)}`)
  },
}
