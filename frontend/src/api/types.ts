export type Kind = 'continuant' | 'occurrent'
export type Rank = 'preferred' | 'normal' | 'deprecated'
export type RangeKind =
  | 'entity' | 'string' | 'number' | 'datetime' | 'geo' | 'json' | 'quantity'

export interface EntityType {
  id: string
  parent_id: string | null
  kind: Kind
  label: string
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

export interface SearchHit {
  id: string
  label: string | null
  type_id: string
  similarity: number | null
}

export interface TraverseNode {
  entity_id: string
  label: string | null
  type_id: string
  depth: number
  path: string[]
  via: string[]
}

export interface GraphSnapshot {
  nodes: { id: string; type_id: string; label: string | null; degree: number }[]
  edges: {
    id: string
    subject_id: string
    object_id: string
    predicate_id: string
    rank: Rank
    confidence: number
  }[]
  total_nodes: number
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
