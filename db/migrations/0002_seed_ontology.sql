-- Bootstrap-Seed (minimal): nur Person + SocialMediaAccount, Base-Interfaces,
-- minimales Prädikat-Vokabular (Spec §1, §2, §9).
-- Nur dieser Bootstrap schreibt direkt — alles Weitere geht durchs Review-Gate (§7.1).

-- === Typen: bewusst minimal, nur zwei konkrete Continuants (§1.1) ===
-- Abstrakte Wurzeln (Continuant/Occurrent/Agent/Event) bleiben vorerst weg;
-- kommen bei Bedarf kontrolliert übers Gate zurück.
INSERT INTO entity_type (id, parent_id, kind, label, wikidata_qid) VALUES
  ('Person',             NULL, 'continuant', 'Person',              'Q5'),
  ('SocialMediaAccount', NULL, 'continuant', 'Social Media Account', NULL);

-- === Base-Interfaces (§2.2) — bleiben vollständig definiert ===
INSERT INTO interface (id, label) VALUES
  ('Nameable',     'Nameable (name, aliases)'),
  ('Locatable',    'Locatable (geo, address)'),
  ('Temporal',     'Temporal (valid_from, valid_to)'),
  ('Quantifiable', 'Quantifiable (value, unit)'),
  ('Embeddable',   'Embeddable (vector)');

INSERT INTO type_implements (type_id, interface_id) VALUES
  ('Person',             'Nameable'),
  ('Person',             'Embeddable'),
  ('SocialMediaAccount', 'Nameable');

-- === Prädikat-Registry: minimales kontrolliertes Vokabular (§2.3) ===
-- inverse_id wird nach dem Insert gesetzt (Paare referenzieren sich gegenseitig).
INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  -- Nameable-Properties (Domain = Interface)
  ('name',        'Name',            NULL, 'Nameable', 'string', NULL, '1:n', false, 'P2561', 'name'),
  ('alias',       'Alias',           NULL, 'Nameable', 'string', NULL, '1:n', false, 'P4970', 'alternateName'),
  -- Harte Identitäts-Keys (deterministisches Dedup, §7.2)
  ('email',       'E-Mail-Adresse',  'Person',             NULL, 'string', NULL, '1:n', true,  'P968',  'email'),
  ('account_uri', 'Account-URI',     'SocialMediaAccount', NULL, 'string', NULL, '1:1', true,  'P2699', 'url'),
  ('handle',      'Handle',          'SocialMediaAccount', NULL, 'string', NULL, '1:1', false, 'P2002', NULL),
  ('platform',    'Plattform',       'SocialMediaAccount', NULL, 'string', NULL, '1:1', false, 'P400',  NULL),
  -- Social Vertical (§9): Person ↔ Account, Person ↔ Person, Account ↔ Account
  ('owns_account', 'besitzt Account', 'Person',             NULL, 'entity', 'SocialMediaAccount', '1:n', false, NULL,    NULL),
  ('account_of',   'Account von',     'SocialMediaAccount', NULL, 'entity', 'Person',             '1:1', false, NULL,    NULL),
  ('knows',        'kennt',           'Person',             NULL, 'entity', 'Person',             'n:m', false, 'P1327', 'knows'),
  ('follows',      'folgt',           'SocialMediaAccount', NULL, 'entity', 'SocialMediaAccount', 'n:m', false, NULL,    'follows'),
  -- Qualifier-Vokabular (§3: Kanten temporal qualifizieren, z. B. knows/follows seit)
  ('since',        'seit',            NULL,                 NULL, 'datetime', NULL,               '1:1', false, 'P580',  'startDate');

-- Inverse Paare (§2.3: automatische Gegenrichtung)
UPDATE predicate SET inverse_id = 'account_of'   WHERE id = 'owns_account';
UPDATE predicate SET inverse_id = 'owns_account' WHERE id = 'account_of';
UPDATE predicate SET inverse_id = 'knows'        WHERE id = 'knows';
