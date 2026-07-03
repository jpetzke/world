-- Occurrent-Vertikale + Social-Content als Continuants (§1.1, §9, §10).
-- Posts/Kommentare sind Continuants (Inhalts-Artefakte); Likes sind Statements
-- mit Valid-Time; Occurrents nur für echt ereignisförmige Dinge (n-är, Zeitspanne).
-- Kontoerstellung = erstellt_am-Statement, Handle-Wechsel = Supersession — beides
-- erscheint auf der Zeitleiste als abgeleiteter Meilenstein, nicht als Entity.

-- (a) Neue Typen. Reihenfolge wegen parent_id-FK. `Ereignis` ist die abstrakte
--     Occurrent-Wurzel (analog Agent für Continuants); Kommentar = Post mit antwort_auf.
INSERT INTO entity_type (id, parent_id, kind, label, abstract, wikidata_qid) VALUES
  ('Ereignis',      NULL,       'occurrent',  'Ereignis',      true,  'Q1656682'),
  ('Kontosperrung', 'Ereignis', 'occurrent',  'Kontosperrung', false, NULL),
  ('Demonstration', 'Ereignis', 'occurrent',  'Demonstration', false, 'Q175331'),
  ('Wahl',          'Ereignis', 'occurrent',  'Wahl',          false, 'Q40231'),
  ('Ort',           NULL,       'continuant', 'Ort',           false, 'Q17334923'),
  ('Post',          NULL,       'continuant', 'Post',          false, NULL);

-- (b) Interfaces: Ereignis-Ast + Ort sind Nameable (Subtypen erben), Ort zusätzlich
--     Locatable. Post bewusst NICHT Nameable — sein Bezeichner ist der Text selbst.
INSERT INTO type_implements (type_id, interface_id) VALUES
  ('Ereignis', 'Nameable'),
  ('Ort',      'Nameable'),
  ('Ort',      'Locatable');

-- (c) Prädikate. Scharfe, typisierte Rollen-Prädikate statt generischem
--     participant+Qualifier (Palantir-Link-Types); Ereigniszeit = beginn/ende-
--     Statements (Provenance + Confidence), NICHT valid_from/valid_to.
INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  -- Social-Content (Track A): Post + Account-Lebenslauf
  ('text',              'Text',              'Post',               NULL, 'string',   NULL,                 '1:1', false, NULL,    'text'),
  ('url',               'URL',               'Post',               NULL, 'string',   NULL,                 '1:1', true,  'P2699', 'url'),
  ('veröffentlicht_am', 'veröffentlicht am', 'Post',               NULL, 'datetime', NULL,                 '1:1', false, 'P577',  'datePublished'),
  ('verfasst_von',      'verfasst von',      'Post',               NULL, 'entity',   'SocialMediaAccount', '1:1', false, 'P50',   'author'),
  ('antwort_auf',       'Antwort auf',       'Post',               NULL, 'entity',   'Post',               '1:1', false, NULL,    'parentItem'),
  ('erwähnt',           'erwähnt',           'Post',               NULL, 'entity',   'SocialMediaAccount', 'n:m', false, NULL,    'mentions'),
  ('gefällt',           'gefällt',           'SocialMediaAccount', NULL, 'entity',   'Post',               'n:m', false, NULL,    NULL),
  ('erstellt_am',       'erstellt am',       'SocialMediaAccount', NULL, 'datetime', NULL,                 '1:1', false, 'P571',  'dateCreated'),
  -- Ort (Place)
  ('koordinaten',       'Koordinaten',       'Ort',                NULL, 'geo',      NULL,                 '1:1', false, 'P625',  'geo'),
  ('teil_von',          'Teil von',          'Ort',                NULL, 'entity',   'Ort',                '1:n', false, 'P131',  NULL),
  -- Ereignis-Wurzel: jedes Ereignis kann Zeit + Ort haben
  ('beginn',            'Beginn',            'Ereignis',           NULL, 'datetime', NULL,                 '1:1', false, 'P580',  'startDate'),
  ('ende',              'Ende',              'Ereignis',           NULL, 'datetime', NULL,                 '1:1', false, 'P582',  'endDate'),
  ('ort',               'Ort',               'Ereignis',           NULL, 'entity',   'Ort',                '1:1', false, 'P276',  'location'),
  -- Kontosperrung
  ('betroffenes_konto', 'betroffenes Konto', 'Kontosperrung',      NULL, 'entity',   'SocialMediaAccount', '1:1', false, NULL,    NULL),
  ('verhängt_von',      'verhängt von',      'Kontosperrung',      NULL, 'entity',   'Platform',           '1:1', false, NULL,    NULL),
  ('grund',             'Grund',             'Kontosperrung',      NULL, 'string',   NULL,                 '1:1', false, NULL,    NULL),
  -- Demonstration
  ('teilnehmer',        'Teilnehmer',        'Demonstration',      NULL, 'entity',   'Agent',              'n:m', false, 'P710',  NULL),
  ('organisiert_von',   'organisiert von',   'Demonstration',      NULL, 'entity',   'Agent',              '1:n', false, 'P664',  'organizer'),
  ('thema',             'Thema',             'Demonstration',      NULL, 'string',   NULL,                 '1:n', false, NULL,    NULL),
  -- Wahl
  ('kandidat',          'Kandidat',          'Wahl',               NULL, 'entity',   'Agent',              'n:m', false, 'P726',  NULL),
  ('gewinner',          'Gewinner',          'Wahl',               NULL, 'entity',   'Agent',              '1:1', false, 'P1346', NULL),
  ('amt',               'Amt',               'Wahl',               NULL, 'string',   NULL,                 '1:1', false, 'P541',  NULL);

-- (d) label_predicate — nach den Prädikat-Inserts (FK). Ereignis bleibt NULL (abstrakt).
UPDATE entity_type SET label_predicate = 'name' WHERE id IN ('Kontosperrung', 'Demonstration', 'Wahl', 'Ort');
UPDATE entity_type SET label_predicate = 'text' WHERE id = 'Post';
