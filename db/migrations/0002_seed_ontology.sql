-- Bootstrap-Seed: Upper Ontology (BFO-Top-Split), Base-Interfaces,
-- Social-Vertical-Typen und kontrolliertes Prädikat-Vokabular (Spec §1, §2, §9).
-- Nur dieser Bootstrap schreibt direkt — alles Weitere geht durchs Review-Gate (§7.1).

-- === Wurzeltypen: Continuant vs. Occurrent (§1.1) ===
INSERT INTO entity_type (id, parent_id, kind, label, wikidata_qid) VALUES
  ('Continuant', NULL,          'continuant', 'Continuant',   NULL),
  ('Occurrent',  NULL,          'occurrent',  'Occurrent',    NULL),
  -- Continuants
  ('Agent',        'Continuant', 'continuant', 'Agent',        'Q24229398'),
  ('Person',       'Agent',      'continuant', 'Person',       'Q5'),
  ('Organization', 'Agent',      'continuant', 'Organization', 'Q43229'),
  ('Company',      'Organization','continuant','Company',      'Q783794'),
  ('Account',      'Continuant', 'continuant', 'Account',      'Q1400264'),
  ('Concept',      'Continuant', 'continuant', 'Concept',      'Q151885'),
  ('Place',        'Continuant', 'continuant', 'Place',        'Q17334923'),
  ('Country',      'Place',      'continuant', 'Country',      'Q6256'),
  ('StockIndex',   'Continuant', 'continuant', 'Stock Index',  'Q1141135'),
  -- Occurrents
  ('Event',           'Occurrent', 'occurrent', 'Event',            'Q1656682'),
  ('Interaction',     'Event',     'occurrent', 'Interaction',      NULL),
  ('Mention',         'Event',     'occurrent', 'Mention',          NULL),
  ('War',             'Event',     'occurrent', 'War',              'Q198'),
  ('Election',        'Event',     'occurrent', 'Election',         'Q40231'),
  ('NaturalDisaster', 'Event',     'occurrent', 'Natural Disaster', 'Q8065'),
  ('StockCrash',      'Event',     'occurrent', 'Stock Market Crash','Q114380');

-- === Base-Interfaces (§2.2) ===
INSERT INTO interface (id, label) VALUES
  ('Nameable',     'Nameable (name, aliases)'),
  ('Locatable',    'Locatable (geo, address)'),
  ('Temporal',     'Temporal (valid_from, valid_to)'),
  ('Quantifiable', 'Quantifiable (value, unit)'),
  ('Embeddable',   'Embeddable (vector)');

INSERT INTO type_implements (type_id, interface_id) VALUES
  ('Person',          'Nameable'),
  ('Person',          'Embeddable'),
  ('Organization',    'Nameable'),
  ('Account',         'Nameable'),
  ('Place',           'Nameable'),
  ('Place',           'Locatable'),
  ('Country',         'Nameable'),
  ('Country',         'Locatable'),
  ('StockIndex',      'Nameable'),
  ('StockIndex',      'Quantifiable'),
  ('StockIndex',      'Temporal'),
  ('Event',           'Temporal'),
  ('Mention',         'Temporal'),
  ('NaturalDisaster', 'Locatable'),
  ('NaturalDisaster', 'Temporal'),
  ('NaturalDisaster', 'Quantifiable');

-- === Prädikat-Registry: kontrolliertes Vokabular (§2.3) ===
-- inverse_id wird nach dem Insert gesetzt (Paare referenzieren sich gegenseitig).

INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  -- Nameable-Properties (Domain = Interface)
  ('name',       'Name',        NULL, 'Nameable', 'string', NULL, '1:n', false, 'P2561', 'name'),
  ('alias',      'Alias',       NULL, 'Nameable', 'string', NULL, '1:n', false, 'P4970', 'alternateName'),
  -- Harte Identitäts-Keys (deterministisches Dedup, §7.2)
  ('email',        'E-Mail-Adresse',  'Person',  NULL, 'string', NULL, '1:n', true,  'P968',  'email'),
  ('wikidata_qid', 'Wikidata-QID',    NULL,      NULL, 'string', NULL, '1:1', true,  NULL,    NULL),
  ('account_uri',  'Account-URI',     'Account', NULL, 'string', NULL, '1:1', true,  'P2699', 'url'),
  ('handle',       'Handle',          'Account', NULL, 'string', NULL, '1:1', false, 'P2002', NULL),
  ('platform',     'Plattform',       'Account', NULL, 'string', NULL, '1:1', false, 'P400',  NULL),
  -- Social Vertical (§9)
  ('knows',               'kennt',                'Person', NULL, 'entity', 'Person',       'n:m', false, 'P1327', 'knows'),
  ('romantic_partner_of', 'Partner/in von',       'Person', NULL, 'entity', 'Person',       'n:m', false, 'P451',  NULL),
  ('works_at',            'arbeitet bei',         'Person', NULL, 'entity', 'Organization', 'n:m', false, 'P108',  'worksFor'),
  ('employs',             'beschäftigt',          'Organization', NULL, 'entity', 'Person', 'n:m', false, NULL,    'employee'),
  ('owns_account',        'besitzt Account',      'Agent',  NULL, 'entity', 'Account',      '1:n', false, NULL,    NULL),
  ('account_of',          'Account von',          'Account',NULL, 'entity', 'Agent',        '1:1', false, NULL,    NULL),
  ('mentions',            'erwähnt',              'Mention',NULL, 'entity', NULL,           'n:m', false, NULL,    'mentions'),
  ('subject_of',          'Subjekt von',          NULL,     NULL, 'entity', 'Mention',      'n:m', false, NULL,    'subjectOf'),
  ('text',                'Text/Snippet',         'Mention',NULL, 'string', NULL,           '1:1', false, NULL,    'text'),
  -- Locatable-Property (Geo, §0/§2.2)
  ('coordinates', 'Koordinaten', NULL, 'Locatable', 'geo', NULL, '1:1', false, 'P625', 'geo'),
  -- Qualifier-Vokabular (§3: works_at + role statt Spezial-Prädikat)
  ('role',  'Rolle',            NULL, NULL, 'string', NULL, '1:1', false, 'P2868', 'roleName'),
  ('hours', 'Wochenstunden',    NULL, NULL, 'number', NULL, '1:1', false, NULL,    NULL),
  -- Cross-Domain-Vokabular (Finance/Geo, §10)
  ('invests_in',   'investiert in',   'Agent', NULL, 'entity', 'Organization', 'n:m', false, NULL, NULL),
  ('has_investor', 'hat Investor',    'Organization', NULL, 'entity', 'Agent', 'n:m', false, 'P1951', NULL),
  ('affected_by',  'betroffen von',   NULL,    NULL, 'entity', 'Event',        'n:m', false, 'P1479', NULL),
  ('located_in',   'befindet sich in',NULL,    NULL, 'entity', 'Place',        'n:m', false, 'P131',  'location'),
  ('at_war_with',  'im Krieg mit',    'Country', NULL, 'entity', 'Country',    'n:m', false, NULL,    NULL),
  ('price',        'Kurs/Preis',      NULL, 'Quantifiable', 'quantity', NULL,  '1:n', false, 'P2284', 'price');

-- Inverse Paare (§2.3: automatische Gegenrichtung)
UPDATE predicate SET inverse_id = 'knows'               WHERE id = 'knows';
UPDATE predicate SET inverse_id = 'romantic_partner_of' WHERE id = 'romantic_partner_of';
UPDATE predicate SET inverse_id = 'at_war_with'         WHERE id = 'at_war_with';
UPDATE predicate SET inverse_id = 'employs'             WHERE id = 'works_at';
UPDATE predicate SET inverse_id = 'works_at'            WHERE id = 'employs';
UPDATE predicate SET inverse_id = 'account_of'          WHERE id = 'owns_account';
UPDATE predicate SET inverse_id = 'owns_account'        WHERE id = 'account_of';
UPDATE predicate SET inverse_id = 'subject_of'          WHERE id = 'mentions';
UPDATE predicate SET inverse_id = 'mentions'            WHERE id = 'subject_of';
UPDATE predicate SET inverse_id = 'has_investor'        WHERE id = 'invests_in';
UPDATE predicate SET inverse_id = 'invests_in'          WHERE id = 'has_investor';
