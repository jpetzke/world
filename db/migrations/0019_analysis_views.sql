-- Whitelist-Views für welt_sql (read-only Escape Hatch der Analyse-Tools).
--
-- Rationale (§14): Kein neues Meta-Modell, keine neue Wahrheit — die Views
-- sind reine, jederzeit neu berechenbare Projektionen der internen Tabellen
-- (Invariante 1) und bilden deren Struktur STABIL und dokumentiert ab. Das
-- Tool welt_sql erlaubt ausschließlich SELECT über genau diese vier Views;
-- interne Tabellen (statement, entity, …) bleiben unerreichbar, damit
-- Schema-Interna nie zur API werden. Binär-/Vektorspalten (value_geo,
-- embedding) und raw-Payloads werden bewusst nicht exponiert.

CREATE VIEW v_entities AS
SELECT id, type_id, label, merged_into, created_at
FROM entity;

COMMENT ON VIEW v_entities IS
  'Entity-Anker. merged_into IS NOT NULL = Dublette (kanonischer Anker steht in merged_into); für die aktuelle Sicht mit merged_into IS NULL filtern.';
COMMENT ON COLUMN v_entities.id IS 'Entity-UUID';
COMMENT ON COLUMN v_entities.type_id IS 'Registry-Typ (entity_type)';
COMMENT ON COLUMN v_entities.label IS 'Anzeige-Bezeichner (denormalisierter Cache)';
COMMENT ON COLUMN v_entities.merged_into IS 'Kanonischer Anker, falls gemerged; NULL = selbst kanonisch';
COMMENT ON COLUMN v_entities.created_at IS 'Anlagezeitpunkt';

CREATE VIEW v_statements AS
SELECT s.id, s.subject_id, subj.label AS subject_label,
       s.predicate_id,
       s.object_id, obj.label AS object_label,
       s.value_type, s.value_text, s.value_number, s.value_unit,
       s.value_datetime, ST_AsGeoJSON(s.value_geo)::jsonb AS value_geojson,
       s.value_json,
       s.rank, s.confidence, s.origin,
       s.valid_from, s.valid_to, s.system_from, s.system_to
FROM statement s
JOIN entity subj ON subj.id = s.subject_id
LEFT JOIN entity obj ON obj.id = s.object_id;

COMMENT ON VIEW v_statements IS
  'Reifizierte Statements (bitemporal). Aktuelle Sicht: system_to IS NULL AND rank <> ''deprecated''. Wert liegt je nach value_type in object_id/value_text/value_number(+value_unit)/value_datetime/value_geojson/value_json.';
COMMENT ON COLUMN v_statements.rank IS 'preferred | normal | deprecated';
COMMENT ON COLUMN v_statements.confidence IS 'Vertrauen 0..1';
COMMENT ON COLUMN v_statements.origin IS 'asserted | inferred';
COMMENT ON COLUMN v_statements.valid_from IS 'Gültigkeit der Behauptung ab (Valid Time)';
COMMENT ON COLUMN v_statements.valid_to IS 'Gültigkeit der Behauptung bis (Valid Time)';
COMMENT ON COLUMN v_statements.system_from IS 'Zeile bekannt seit (Transaction Time)';
COMMENT ON COLUMN v_statements.system_to IS 'Zeile abgelöst am; NULL = aktuell';

CREATE VIEW v_qualifiers AS
SELECT id, statement_id, predicate_id, value_type,
       value_text, value_number, value_datetime, object_id
FROM qualifier;

COMMENT ON VIEW v_qualifiers IS
  'Qualifier verfeinern ein Statement (statement_id) mit Registry-Prädikaten.';

CREATE VIEW v_sources AS
SELECT d.id, d.url, d.activity, d.agent, d.retrieved_at,
       r.statement_id
FROM source_document d
LEFT JOIN reference r ON r.source_id = d.id;

COMMENT ON VIEW v_sources IS
  'Quellen (Provenance) inkl. Zuordnung zu Statements: eine Zeile pro (Quelle, belegtes Statement); statement_id IS NULL = Quelle ohne Belege. raw-Payload bewusst nicht exponiert (welt_source liefert ihn).';
