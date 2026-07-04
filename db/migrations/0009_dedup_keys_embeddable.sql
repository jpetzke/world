-- Registry-Review-Fixes (§14): Dedup-Pfade für Organization/Ort, Embeddable
-- wird semantisch tragend, `since` entfällt.
--
-- Rationale (Entscheidungsbaum §14.1, Prädikat-Design §14.3):
-- (1) `since` raus: Das Gate lehnt domainlose Prädikate hart ab (registry.py,
--     „Prädikat braucht domain") — `since` existierte nur, weil der Bootstrap
--     direkt schrieb, und umging als domainloses Haupt-Prädikat zudem den
--     Domain-Shape-Check. Zeitliche Gültigkeit einer Beziehung ist
--     Valid-Time-Job (valid_from/valid_to am Statement), kein eigenes
--     Qualifier-Vokabular. Qualifier nutzen reguläre Registry-Prädikate dual
--     (Wikidata-Praxis: P580 hängt dort als Qualifier an Statements) —
--     `beginn` übernimmt diese Rolle. Nebeneffekt: P580 mappt wieder 1:1.
--     Kein DELETE-Guard nötig: Phase 0, kein Statement/Qualifier nutzt es;
--     der FK auf qualifier.predicate_id ließe die Migration sonst laut
--     scheitern statt Daten zu verlieren.
-- (2) Organization und Ort hatten keinen Dedup-Pfad — Verstoß gegen §14.5
--     („jeder aus Quellen befüllte Typ braucht ≥1 identifying-Prädikat"):
--     `website_url` (P856) für Organization, `wikidata_qid` für Ort.
--     Koordinaten taugen nicht als harter Key (Stufe-1-Resolve matcht
--     value_text; zwei Quellen liefern selten bitidentische Punkte).
--     Domain von `wikidata_qid` bewusst eng (Ort); anheben, wenn weitere
--     Äste QIDs führen.
-- (3) Embeddable trägt jetzt Semantik: create_entity embeddet nur noch
--     Typen, die das Interface implementieren (vorher: jede Entity mit
--     Label, Interface war totes Etikett). Alle fuzzy-dedup- bzw.
--     such-relevanten Typen implementieren es — Agent-Ast, Account, Post,
--     Ereignis-Ast, Ort. Platform bewusst nicht: statisch kuratierter
--     Bestand, Lookup per exaktem Label (follower_import).

-- (1) since entfernen
DELETE FROM predicate WHERE id = 'since';

-- (2) Harte Identity-Keys (§7.2)
INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  ('website_url',  'Website',      'Organization', NULL, 'string', NULL, '1:1', true, 'P856', 'url'),
  ('wikidata_qid', 'Wikidata-QID', 'Ort',          NULL, 'string', NULL, '1:1', true, NULL,   NULL);

-- (3) Embeddable-Implementierungen. Vererbung über type_interfaces:
--     Agent → Person/Organization (Person hat es aus 0002 bereits direkt),
--     Ereignis → Kontosperrung/Demonstration/Wahl.
INSERT INTO type_implements (type_id, interface_id) VALUES
  ('Agent',              'Embeddable'),
  ('SocialMediaAccount', 'Embeddable'),
  ('Post',               'Embeddable'),
  ('Ereignis',           'Embeddable'),
  ('Ort',                'Embeddable');

-- Platform ist nicht mehr Embeddable; Embedding ist ableitbarer Cache
-- (Invariante 1) → zurücksetzen.
UPDATE entity SET embedding = NULL WHERE type_id = 'Platform';
