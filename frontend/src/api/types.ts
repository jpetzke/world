export type Kind = 'continuant' | 'occurrent'
export type Rank = 'preferred' | 'normal' | 'deprecated'
export type RangeKind =
  | 'entity' | 'string' | 'number' | 'datetime' | 'geo' | 'json' | 'quantity'

export interface EntityType {
  id: string
  parent_id: string | null
  kind: Kind
  label: string
  abstract: boolean
  /** Prädikat, das den Anzeige-Bezeichner trägt (z. B. Person→name, Account→handle). */
  label_predicate: string | null
  wikidata_qid: string | null
}

export interface InterfaceDef {
  id: string
  label: string
}

export interface Predicate {
  id: string
  label: string
  domain_type: string | null
  domain_interface: string | null
  range_kind: RangeKind
  range_type: string | null
  cardinality: '1:1' | '1:n' | 'n:m' | null
  inverse_id: string | null
  identifying: boolean
  wikidata_pid: string | null
  schema_org: string | null
}

export interface Vocabulary {
  types: EntityType[]
  interfaces: InterfaceDef[]
  predicates: Predicate[]
  implementations: { type_id: string; interface_id: string }[]
}

export interface Entity {
  id: string
  type_id: string
  label: string | null
  merged_into: string | null
  created_at: string
}

export interface Qualifier {
  id: string
  predicate_id: string
  value_type: string
  value_text: string | null
  value_number: number | null
  value_datetime: string | null
  object_id: string | null
}

export interface Reference {
  id: string
  url: string | null
  activity: string | null
  agent: string | null
  retrieved_at: string | null
}

export interface Statement {
  id: string
  subject_id: string
  predicate_id: string
  value_type: RangeKind
  object_id: string | null
  object_label: string | null
  object_type: string | null
  subject_label?: string | null
  subject_type?: string | null
  value_text: string | null
  value_number: number | null
  value_unit: string | null
  value_datetime: string | null
  value_json: unknown
  value_geojson: { type: string; coordinates: [number, number] } | null
  rank: Rank
  confidence: number
  origin: 'asserted' | 'inferred'
  valid_from: string | null
  valid_to: string | null
  system_from: string
  system_to: string | null
  qualifiers?: Qualifier[]
  references?: Reference[]
}

export interface EntityView {
  entity: Entity
  statements: Statement[]
  incoming: Statement[]
}

/** Eintrag der Entity-Zeitleiste: echtes Ereignis (◆) oder abgeleiteter
 * Meilenstein (○) aus datetime-Statements bzw. Label-Wechseln. */
export type TimelineItem =
  | {
      kind: 'ereignis'
      entity_id: string
      label: string | null
      type_id: string
      via: string[]
      beginn: string | null
      ende: string | null
      at: string | null
    }
  | {
      kind: 'meilenstein'
      predicate_id: string
      predicate_label: string
      at: string | null
      detail: string | null
    }

export interface SearchHit {
  id: string
  label: string | null
  type_id: string
  similarity: number | null
}

export interface GraphNodeDTO {
  id: string
  type_id: string
  label: string | null
  degree: number
  /** Hop-Distanz vom Startknoten (nur in der Ego-Sicht gesetzt). */
  depth?: number
  /** Persistierte Layout-Position (Skeleton/Pfad; null = nie gespeichert). */
  x?: number | null
  y?: number | null
}

/** Skeleton-Node: DoI-Auswahl mit vorberechneten Metriken (Migration 0018). */
export interface SkeletonNodeDTO extends GraphNodeDTO {
  community: number | null
  pagerank: number | null
}

export interface Skeleton {
  nodes: SkeletonNodeDTO[]
  edges: GraphEdgeDTO[]
  total_nodes: number
  metrics_at: string | null
}

export interface GraphPath {
  found: boolean
  nodes: GraphNodeDTO[]
  edges: GraphEdgeDTO[]
}

export interface GraphEdgeDTO {
  id: string
  subject_id: string
  object_id: string
  predicate_id: string
  rank: Rank
  confidence: number
}

export interface GraphSnapshot {
  nodes: GraphNodeDTO[]
  edges: GraphEdgeDTO[]
  total_nodes: number
}

/** k-Hop-Nachbarschaft als induzierter Teilgraph (ungerichtet). */
export interface Neighborhood extends GraphSnapshot {
  start_id: string
}

export interface Stats {
  entities: number
  statements: number
  sources: number
  pending_proposals: number
  by_type: { type_id: string; n: number }[]
}

export interface EntityListItem {
  id: string
  type_id: string
  label: string | null
  created_at: string
  statement_count: number
}

export interface SourceListItem {
  id: string
  url: string | null
  retrieved_at: string | null
  activity: string | null
  agent: string | null
  statement_count: number
  file_name?: string | null
  file_mime?: string | null
  file_size?: number | null
}

export interface SourceFileMeta {
  filename: string
  mime: string
  size_bytes: number
  sha256: string
  created_at: string
}

export interface SourceDoc extends SourceListItem {
  raw: unknown
}

export interface SourceDetail {
  source: SourceDoc
  statements: (Statement & { subject_label: string | null })[]
  file?: SourceFileMeta | null
}

export interface ResolveResult {
  match: string | null
  method: string | null
  candidates: { id: string; label: string | null; type_id: string; similarity: number }[]
}

export interface PipelineReport {
  source_id: string
  committed: string[]
  rejected: { predicate?: string; proposal?: string; problems: string[] }[]
  proposals: { kind: string; id: string; predicate_id?: string; type_id?: string }[]
  entities_created: string[]
}

export interface TypeProposal {
  id: string
  type_id: string
  parent_id: string
  kind: Kind
  label: string
  interfaces: string[]
  wikidata_qid: string | null
  rationale: string | null
  proposed_by: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export interface PredicateProposal {
  id: string
  predicate_id: string
  label: string
  domain_type: string | null
  domain_interface: string | null
  range_kind: RangeKind
  range_type: string | null
  cardinality: string | null
  inverse_id: string | null
  rationale: string | null
  proposed_by: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export interface Proposals {
  types: TypeProposal[]
  predicates: PredicateProposal[]
}

/** Polymorpher Wert, wie ihn POST /api/statements erwartet (§3.1). */
export type ValuePayload =
  | { type: 'entity'; object_id: string }
  | { type: 'string'; text: string }
  | { type: 'number'; number: number }
  | { type: 'quantity'; number: number; unit: string }
  | { type: 'datetime'; datetime: string }
  | { type: 'geo'; lat: number; lon: number }
  | { type: 'json'; json: unknown }

// --- Follower-Listen-Import --------------------------------------------------

export interface FollowerRowIn {
  username: string
  display_name: string | null
}

export type FollowerRowStatus = 'new_account' | 'new_follow' | 'confirmed' | 'invalid'

export interface FollowerPreviewRow {
  username: string
  display_name: string | null
  status: FollowerRowStatus
  reason?: string
  entity_id?: string
}

export interface FollowerListPreview {
  rows: FollowerPreviewRow[]
  summary: {
    total: number
    new_account: number
    new_follow: number
    confirmed: number
    invalid: number
  }
}

export interface FollowerListCommitResult {
  source_id: string
  accounts_created: number
  follows_created: number
  follows_confirmed: number
  skipped_invalid: number
}

// --- Instagram-JSON-Upload (Scraper-Format) ----------------------------------

export interface InstagramFilePreview {
  filename: string
  error?: string
  owner_handle?: string
  owner_exists?: boolean
  direction?: 'followers' | 'following'
  captured_at?: string | null
  status?: string | null
  expected_total?: number | null
  rows_total?: number
  valid?: number
  invalid?: number
  accounts_new?: number
  accounts_existing?: number
  follows_new?: number
  follows_confirmed?: number
}

export interface InstagramPreviewResult {
  files: InstagramFilePreview[]
  totals: {
    files: number
    files_failed: number
    rows_total: number
    valid: number
    invalid: number
    accounts_new: number
    accounts_existing: number
    follows_new: number
    follows_confirmed: number
  }
}

export interface InstagramFileCommit {
  filename: string
  error?: string
  source_id?: string
  owner_handle?: string
  owner_created?: boolean
  direction?: 'followers' | 'following'
  captured_at?: string | null
  status?: string | null
  expected_total?: number | null
  rows_total?: number
  accounts_created?: number
  follows_created?: number
  follows_confirmed?: number
  skipped_invalid?: number
  skipped_conflict?: number
}

export interface InstagramCommitResult {
  files: InstagramFileCommit[]
  totals: {
    files: number
    files_failed: number
    accounts_created: number
    follows_created: number
    follows_confirmed: number
    skipped_invalid: number
    skipped_conflict: number
  }
}

export type ApiKeyScope = 'read' | 'write' | 'admin'

export interface ApiKey {
  id: string
  name: string
  secret: string
  scope: ApiKeyScope
  created_at: string
  rotated_at: string | null
  last_used_at: string | null
}
