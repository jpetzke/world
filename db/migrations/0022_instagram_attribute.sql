-- Instagram-Ingest: Attribut- und Identitäts-Vokabular für den JSON-Upload
-- (Scraper-Format metadata + users[]). Reine Registry-INSERTs (Daten), kein
-- Schema-DDL — Erweiterung nach §14.5 (geplant/designt → kuratierte Migration).
--
-- Modellierung (Entscheidungsbaum §14.1): alles hier sind binäre Attribute
-- bzw. Messwerte AM bestehenden Continuant `SocialMediaAccount` → Statements,
-- keine neuen Typen, kein Occurrent. Die Beziehung `follows` existiert bereits
-- (0002). Werte-Polymorphie (§3.1): kein neuer value_type nötig.
--
-- (1) instagram_pk — die stabile numerische Instagram-User-ID. Der Username
--     (handle) ist mutabel und als identifying-Key fragil (Handle-Wechsel,
--     Username-Recycling); die pk ist immutabel und global eindeutig → der
--     korrekte Identitäts-Anker (§7.2, „≥1 identifying-Prädikat"). Optional:
--     die pk ist nicht in jeder Quelle vorhanden — account_uri bleibt der
--     funktionierende Fallback, resolve matcht deterministisch über beide.
--     identifying erfordert range_kind='string' + cardinality '1:1' (0013/0014)
--     → partieller Unique-Index wie in 0014 / registry.ensure_identifying_index.
-- (2) is_verified / is_private — öffentlich sichtbarer Verifizierungs-Badge und
--     Privatsphäre-Status. Das Substrat hat keinen boolean-value_type (0001) →
--     modelliert als number 1/0 (queryfähig über value_number).
-- (3) follower_count / following_count — die von Instagram gemeldeten Zahlen
--     zum Aufnahmezeitpunkt. Als number-Statement mit valid_from=captured_at:
--     eine Zeitreihe über die Valid-Time. Kein Auto-Supersede — eine spätere
--     abweichende Lesung koexistiert als geflaggter Kardinalitätskonflikt
--     (Invariante 4: „ein Flag, kein Reject").
--
-- Idempotent: ON CONFLICT DO NOTHING auf predicate, Index via IF NOT EXISTS.

INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  ('instagram_pk',    'Instagram-User-ID', 'SocialMediaAccount', NULL, 'string', NULL, '1:1', true,  NULL,    NULL),
  ('is_verified',     'verifiziert',       'SocialMediaAccount', NULL, 'number', NULL, '1:1', false, NULL,    NULL),
  ('is_private',      'privat',            'SocialMediaAccount', NULL, 'number', NULL, '1:1', false, NULL,    NULL),
  ('follower_count',  'Follower-Zahl',     'SocialMediaAccount', NULL, 'number', NULL, '1:1', false, 'P8687', NULL),
  ('following_count', 'Following-Zahl',    'SocialMediaAccount', NULL, 'number', NULL, '1:1', false, NULL,    NULL)
ON CONFLICT (id) DO NOTHING;

-- DB-seitiger Dubletten-Schutz für den neuen identifying-Key (0014-Muster).
-- Neues Prädikat → keine Bestandsdaten, kein Konflikt-Check nötig.
CREATE UNIQUE INDEX IF NOT EXISTS statement_ident_instagram_pk_uniq
  ON statement (predicate_id, value_text)
  WHERE predicate_id = 'instagram_pk' AND system_to IS NULL
    AND rank <> 'deprecated';
