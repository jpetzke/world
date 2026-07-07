-- Person: separate Namensbestandteile vorname + nachname.
--
-- Rationale (Entscheidungsbaum §14.1, Prädikat-Design §14.3):
-- (1) Vorname/Nachname sind reine string-Statements auf Person (§14.1
--     Schritt 2: kein eigener Typ, kein Event — Attribute mit Provenance).
--     `name` bleibt unangetastet der Voll-Bezeichner (label_predicate von
--     Person); vorname/nachname dienen Anzeige, Sortierung und Dedup.
-- (2) MEHRERE Vornamen gehen ZUSAMMEN in EIN vorname-Statement („Hans
--     Peter"), das Prädikat heißt trotzdem `vorname` — deshalb cardinality
--     1:1 (ein aktueller Wert pro Person; widersprüchliche Quellen
--     koexistieren wie überall via Flag + Rank, §6). Bewusst NICHT das
--     Wikidata-Modell „ein P735-Statement pro Einzelnamen" — dort sind
--     Vornamen eigene Items, hier wäre das Typ-/Statement-Explosion ohne
--     Query-Nutzen.
-- (3) Domain Person, nicht Agent/Nameable: Organisationen haben keine
--     Vor-/Nachnamen. Range string, kein identifying (Namen sind kein
--     Dedup-Key). wikidata_pid/schema_org gemappt (P735/givenName,
--     P734/familyName), Wertsemantik siehe (2).
-- (4) Bestehende name-Statements werden NICHT automatisch gesplittet —
--     Aufteilen ist Kurations-/Extraktions-Arbeit mit eigener Provenance,
--     kein Migrations-Nebeneffekt.
-- Idempotent via ON CONFLICT DO NOTHING.

INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  ('vorname',  'Vorname',  'Person', NULL, 'string', NULL, '1:1', false, 'P735', 'givenName'),
  ('nachname', 'Nachname', 'Person', NULL, 'string', NULL, '1:1', false, 'P734', 'familyName')
ON CONFLICT (id) DO NOTHING;
