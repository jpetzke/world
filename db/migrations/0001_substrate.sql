-- Weltmodell-Substrat: reifizierter Statement-Store (Spec §8)
-- Eine Source of Truth: PostgreSQL + pgvector + PostGIS.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

-- === REGISTRY (Schema-als-Daten, §2) ===

CREATE TABLE entity_type (
  id           text PRIMARY KEY,          -- 'Person', 'War', ...
  parent_id    text REFERENCES entity_type(id),
  kind         text NOT NULL CHECK (kind IN ('continuant','occurrent')),
  label        text NOT NULL,
  wikidata_qid text
);

CREATE TABLE interface (
  id    text PRIMARY KEY,                 -- 'Nameable', 'Locatable', ...
  label text NOT NULL
);

CREATE TABLE type_implements (
  type_id      text REFERENCES entity_type(id),
  interface_id text REFERENCES interface(id),
  PRIMARY KEY (type_id, interface_id)
);

CREATE TABLE predicate (
  id               text PRIMARY KEY,      -- 'works_at', 'invests_in', ...
  label            text NOT NULL,
  domain_type      text REFERENCES entity_type(id),
  domain_interface text REFERENCES interface(id),  -- Alternative: Interface als Domain (§2.3)
  range_kind       text NOT NULL CHECK (range_kind IN
                     ('entity','string','number','datetime','geo','json','quantity')),
  range_type       text REFERENCES entity_type(id),  -- falls range_kind='entity'
  cardinality      text CHECK (cardinality IN ('1:1','1:n','n:m')),
  inverse_id       text REFERENCES predicate(id),
  identifying      boolean NOT NULL DEFAULT false,   -- harter Dedup-Key (§7.2)
  wikidata_pid     text,
  schema_org       text
);

-- === ENTITIES (nur Identitäts-Anker) ===

CREATE TABLE entity (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_id     text NOT NULL REFERENCES entity_type(id),
  label       text,                       -- denormalisierter Cache, kein SoT
  embedding   vector(1024),               -- pgvector, für Dedup & Suche
  merged_into uuid REFERENCES entity(id), -- gesetzt durch merge_entity (§7.2), kein Datenverlust
  created_at  timestamptz DEFAULT now()
);

-- === PROVENANCE (§5) ===

CREATE TABLE source_document (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  url          text,
  retrieved_at timestamptz,
  activity     text,                      -- 'apify:linkedin', 'n8n:exec:123', ...
  agent        text,                      -- Pipeline / Modellname
  raw          jsonb
);

-- === STATEMENT (das reifizierte Tripel, §3) ===

CREATE TABLE statement (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id     uuid NOT NULL REFERENCES entity(id),
  predicate_id   text NOT NULL REFERENCES predicate(id),

  value_type     text NOT NULL CHECK (value_type IN
                   ('entity','string','number','datetime','geo','json','quantity')),
  object_id      uuid REFERENCES entity(id),
  value_text     text,
  value_number   numeric,
  value_unit     text,
  value_datetime timestamptz,
  value_geo      geography,
  value_json     jsonb,

  rank           text DEFAULT 'normal' CHECK (rank IN ('preferred','normal','deprecated')),
  confidence     real DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  origin         text DEFAULT 'asserted' CHECK (origin IN ('asserted','inferred')),

  valid_from     timestamptz,             -- Valid Time (§4)
  valid_to       timestamptz,
  system_from    timestamptz DEFAULT now(), -- Transaction Time (§4)
  system_to      timestamptz,             -- NULL = aktuell

  CHECK (value_type <> 'entity' OR object_id IS NOT NULL)
);

CREATE TABLE qualifier (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  statement_id uuid NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  predicate_id text NOT NULL REFERENCES predicate(id),
  value_type   text NOT NULL,
  value_text   text, value_number numeric, value_datetime timestamptz,
  object_id    uuid REFERENCES entity(id)
);

CREATE TABLE reference (
  statement_id uuid NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  source_id    uuid NOT NULL REFERENCES source_document(id),
  PRIMARY KEY (statement_id, source_id)
);

CREATE INDEX statement_subject_predicate_idx ON statement (subject_id, predicate_id);
CREATE INDEX statement_object_idx ON statement (object_id);
CREATE INDEX entity_embedding_idx ON entity USING hnsw (embedding vector_cosine_ops);
CREATE INDEX entity_type_idx ON entity (type_id);
CREATE INDEX qualifier_statement_idx ON qualifier (statement_id);

-- === REVIEW-GATE für neue Typen/Prädikate (§7.1) ===
-- Der Extraktor schreibt nie frei: proposed_* geht durchs Gate, approve
-- erzwingt Registry-Regeln (Parent+Interfaces bzw. domain/range/cardinality).

CREATE TABLE proposed_type (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_id      text NOT NULL,
  parent_id    text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('continuant','occurrent')),
  label        text NOT NULL,
  interfaces   text[] NOT NULL DEFAULT '{}',
  wikidata_qid text,
  rationale    text,
  proposed_by  text NOT NULL,             -- Agent (Mensch oder Pipeline)
  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','approved','rejected')),
  created_at   timestamptz DEFAULT now(),
  decided_at   timestamptz
);

CREATE TABLE proposed_predicate (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  predicate_id     text NOT NULL,
  label            text NOT NULL,
  domain_type      text,
  domain_interface text,
  range_kind       text NOT NULL CHECK (range_kind IN
                     ('entity','string','number','datetime','geo','json','quantity')),
  range_type       text,
  cardinality      text CHECK (cardinality IN ('1:1','1:n','n:m')),
  inverse_id       text,
  wikidata_pid     text,
  schema_org       text,
  rationale        text,
  proposed_by      text NOT NULL,
  status           text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected')),
  created_at       timestamptz DEFAULT now(),
  decided_at       timestamptz
);
