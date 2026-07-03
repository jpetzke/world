-- Oberontologie: abstrakter Agent-Knoten über Person + Organization, Platform
-- als Untertyp von Organization. `platform`-Prädikat von Freitext (string) auf
-- eine kontrollierte Auswahl (entity → Platform) umgestellt (§2.1, §2.3).
-- `kind` bleibt das Split-Etikett; es gibt bewusst KEINEN Typ `Continuant`.

-- (a) abstract-Flag: abstrakte Typen erscheinen nicht im Anlegen-Grid.
--     Default false → alle bestehenden Typen bleiben anlegbar.
ALTER TABLE entity_type ADD COLUMN abstract boolean NOT NULL DEFAULT false;

-- (b) Neue Typen (alle continuant). Reihenfolge wegen parent_id-FK.
INSERT INTO entity_type (id, parent_id, kind, label, abstract, wikidata_qid) VALUES
  ('Agent',        NULL,           'continuant', 'Agent',        true,  'Q24229398'),
  ('Organization', 'Agent',        'continuant', 'Organization', false, 'Q43229'),
  ('Platform',     'Organization', 'continuant', 'Platform',     false, 'Q7397');

-- (c) Person unter Agent hängen (SocialMediaAccount bleibt Wurzel — kein Akteur).
UPDATE entity_type SET parent_id = 'Agent' WHERE id = 'Person';

-- (d) Nameable auf Agent → Person/Organization/Platform erben „hat Namen".
INSERT INTO type_implements (type_id, interface_id) VALUES ('Agent', 'Nameable');

-- (e) platform: string → entity(Platform). Kein Statement nutzt es → delete+recreate sicher.
DELETE FROM predicate WHERE id = 'platform';
INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  ('platform', 'Plattform', 'SocialMediaAccount', NULL, 'entity', 'Platform', '1:1', false, 'P400', NULL);

-- (f) Starter-Plattformen als Entities (Identitäts-Anker brauchen keine Provenance).
INSERT INTO entity (type_id, label) VALUES
  ('Platform', 'Instagram'),
  ('Platform', 'Reddit'),
  ('Platform', 'TikTok'),
  ('Platform', 'YouTube'),
  ('Platform', 'X'),
  ('Platform', 'LinkedIn'),
  ('Platform', 'Facebook');
