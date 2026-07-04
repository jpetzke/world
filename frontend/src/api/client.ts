import type {
  Entity, EntityListItem, EntityView, FollowerListCommitResult,
  FollowerListPreview, FollowerRowIn, GraphSnapshot, Neighborhood,
  PipelineReport, Proposals, ResolveResult, SearchHit, SourceDetail,
  SourceDoc, SourceFileMeta, SourceListItem, Statement, Stats, TimelineItem,
  ValuePayload, Vocabulary,
} from './types'

/** Gate-Rejects (422) tragen eine Problems-Liste — die zeigen wir inline. */
export class ApiError extends Error {
  status: number
  problems: string[]

  constructor(status: number, detail: unknown) {
    const problems = Array.isArray(detail)
      ? detail.map(String)
      : [typeof detail === 'string' ? detail : JSON.stringify(detail)]
    super(problems.join('; '))
    this.status = status
    this.problems = problems
  }
}

/** Wird bei 401 auf geschützten Routen gefeuert (Session abgelaufen) →
 *  App hängt sich ein und wirft zurück auf die Login-Ansicht. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, {
    ...init,
    // FormData bekommt seinen multipart-Header vom Browser (mit boundary).
    headers: typeof init?.body === 'string' ? { 'content-type': 'application/json' } : undefined,
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* kein JSON-Body */
    }
    // Session weg: nicht bei den Auth-Routen selbst (dort ist 401 erwartet).
    if (res.status === 401 && !path.startsWith('/auth/')) onUnauthorized?.()
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body),
})

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export type AuthState = { authenticated: boolean; username: string }

export const api = {
  auth: {
    me: () => req<AuthState>('/auth/me'),
    login: (username: string, password: string) =>
      req<AuthState>('/auth/login', post({ username, password })),
    logout: () => req<{ authenticated: false }>('/auth/logout', post({})),
  },

  stats: () => req<Stats>('/stats'),
  graph: (maxNodes = 400) => req<GraphSnapshot>(`/graph${qs({ max_nodes: maxNodes })}`),
  vocabulary: () => req<Vocabulary>('/registry/vocabulary'),
  proposals: (status: string) => req<Proposals>(`/registry/proposals${qs({ status })}`),
  proposeType: (body: unknown) => req('/registry/proposals/types', post(body)),
  proposePredicate: (body: unknown) => req('/registry/proposals/predicates', post(body)),
  decideProposal: (kind: 'types' | 'predicates', id: string, decision: 'approve' | 'reject') =>
    req(`/registry/proposals/${kind}/${id}/${decision}`, post({})),

  search: (q: string, typeId?: string) =>
    req<SearchHit[]>(`/search${qs({ q, type_id: typeId })}`),
  listEntities: (params: { type_id?: string; q?: string; limit?: number; offset?: number }) =>
    req<{ items: EntityListItem[]; total: number }>(`/entities${qs(params)}`),
  entity: (id: string, params?: { system_at?: string; valid_at?: string; include_deprecated?: boolean }) =>
    req<EntityView>(`/entities/${id}${qs(params ?? {})}`),
  timeline: (id: string) => req<TimelineItem[]>(`/entities/${id}/timeline`),
  createEntity: (body: { type_id: string; label: string }) =>
    req<Entity>('/entities', post(body)),
  resolve: (body: { type_id: string; label?: string; identifiers?: Record<string, string> }) =>
    req<ResolveResult>('/resolve', post(body)),
  merge: (sourceId: string, targetId: string) =>
    req(`/entities/${sourceId}/merge`, post({ target_id: targetId })),

  createSource: (body: { activity: string; agent: string; url?: string; raw?: unknown }) =>
    req<{ id: string }>('/sources', post(body)),
  listSources: (params: { limit?: number; offset?: number }) =>
    req<{ items: SourceListItem[]; total: number }>(`/sources${qs(params)}`),
  source: (id: string) => req<SourceDetail>(`/sources/${id}`),
  uploadSource: (file: File, activity: string, url?: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('activity', activity)
    if (url) form.append('url', url)
    return req<{ source: SourceDoc; file: SourceFileMeta }>('/sources/upload', {
      method: 'POST', body: form,
    })
  },
  fileUrl: (id: string) => `/api/sources/${id}/file`,

  createStatement: (body: {
    subject_id: string
    predicate_id: string
    value: ValuePayload
    source_ids: string[]
    rank?: string
    confidence?: number
    valid_from?: string | null
    valid_to?: string | null
    qualifiers?: { predicate_id: string; value: ValuePayload }[]
  }) => req<Statement>('/statements', post(body)),
  deprecateStatement: (id: string, validTo?: string | null) =>
    req<Statement>(`/statements/${id}/deprecate`, post({ valid_to: validTo ?? null })),
  setRank: (id: string, rank: string) =>
    req<Statement>(`/statements/${id}/rank`, post({ rank })),

  traverse: (body: {
    start_id: string; max_depth: number
    predicates?: string[] | null; max_nodes?: number
  }) => req<Neighborhood>('/query/traverse', post(body)),

  followerListPreview: (body: {
    owner_entity_id: string
    direction: 'followers' | 'following'
    rows: FollowerRowIn[]
  }) => req<FollowerListPreview>('/ingest/follower-list/preview', post(body)),
  followerListCommit: (body: {
    owner_entity_id: string
    direction: 'followers' | 'following'
    rows: FollowerRowIn[]
    observed_at?: string | null
  }) => req<FollowerListCommitResult>('/ingest/follower-list/commit', post(body)),

  ingest: (body: {
    activity: string
    agent: string
    raw: unknown
    url?: string
    extractor: string
  }) => req<{ source: SourceDoc; pipeline: PipelineReport | null }>('/ingest', post(body)),
}
