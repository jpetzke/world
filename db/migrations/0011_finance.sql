-- Finance-Vertical (Welt-Wissen): Unternehmen, Wertpapier, Übernahme (§10, Phase 3).
-- Ziel: Cross-Domain-Kette Person → Unternehmen → Ereignis → Ort über EINE Struktur.
-- Quellen: News + LLM-Extraktion, manuelle Kuratierung. Markt-Zeitreihen (Kurse)
-- bewusst außen vor — Hochvolumen-Zeitreihen sind kein Statement-Material.
--
-- Rationale (Entscheidungsbaum §14.1, Prädikat-Design §14.3, Typ-Checkliste §14.4):
-- (1) Unternehmen = Continuant unter Organization: persistiert mit Identität,
--     sammelt eigene Statements (branche); erbt Nameable + Embeddable über
--     Agent → Organization (Vererbung über Parent-Kette, 0009). Dedup-Pfade:
--     lei (P1278, harter Key für jur. Personen), geerbtes website_url,
--     wikidata_qid (s. (5)).
-- (2) Wertpapier = eigener Continuant, kein Unternehmen-Attribut: eigene
--     Identität (isin, P946), sammelt eigene Statements (ticker, notiert_an,
--     emittiert_von). Subtypen Aktie/Anleihe/ETF wären Typ-Explosion —
--     `gattung` (string) klassifiziert; eigene Subtypen erst, wenn ein Ast
--     eigene Prädikate braucht.
-- (3) Übernahme = Occurrent (§14.1 Schritt 4): Zeitfenster + n-äre Rollen.
--     Scharfe Rollen-Prädikate käufer/übernahmeziel/kaufpreis statt
--     participant+Qualifier (0007-Muster); beginn/ende/ort von Ereignis geerbt.
--     Beteiligungs-Änderung dagegen ist KEIN Event: beteiligt_an-Statement mit
--     Valid-Time + Supersession trägt die Historie (§14.1 Schritt 2).
-- (4) beteiligt_an (Agent → Unternehmen, P1830) mit Qualifier anteil_prozent;
--     arbeitet_bei (P108) und geführt_von (P1037) mit Qualifier rolle (P2868)
--     statt works_at_as_X / ceo_von — Verfeinerung ist Qualifier-Job (§14.3).
--     rolle hängt an Agent (auch als Haupt-Statement legitim: Amt/Funktion).
--     anteil_prozent bekommt Domain-Interface Quantifiable: solange kein Typ
--     das Interface implementiert, ist es nur als Qualifier schreibbar —
--     regelkonform verankert statt domainlos (Lehre aus 0009/`since`).
--     ticker dual: Haupt-Statement am Wertpapier UND Qualifier an notiert_an
--     (Wikidata-Praxis: P249 hängt als Qualifier an P414).
-- (5) wikidata_qid-Domain von Ort auf Nameable angehoben — 0009 sah das vor
--     („anheben, wenn weitere Äste QIDs führen"): News-Extraktion dedupt
--     Unternehmen/Personen am stabilsten über die QID. Bestehende
--     Ort-Statements bleiben gültig (Ort implementiert Nameable).
-- (6) Keine neuen Inversen-Paare: der Extraktor normalisiert auf die
--     kanonische Richtung (Bestandspraxis: nur owns_account/account_of,
--     knows); Inverse sind über inverse_id nachrüstbar, kein Redesign.

-- (a) Neue Typen. Unternehmen hängt in den Agent-Ast, Übernahme in den
--     Ereignis-Ast; Wertpapier ist eigenständige Continuant-Wurzel.
INSERT INTO entity_type (id, parent_id, kind, label, abstract, wikidata_qid) VALUES
  ('Unternehmen', 'Organization', 'continuant', 'Unternehmen', false, 'Q4830453'),
  ('Wertpapier',  NULL,           'continuant', 'Wertpapier',  false, 'Q169489'),
  ('Übernahme',   'Ereignis',     'occurrent',  'Übernahme',   false, 'Q1416898');

-- (b) Interfaces: Unternehmen und Übernahme erben Nameable + Embeddable über
--     ihre Parents (Agent bzw. Ereignis); nur Wertpapier braucht sie direkt.
INSERT INTO type_implements (type_id, interface_id) VALUES
  ('Wertpapier', 'Nameable'),
  ('Wertpapier', 'Embeddable');

-- (c) Prädikate.
INSERT INTO predicate (id, label, domain_type, domain_interface, range_kind, range_type,
                       cardinality, identifying, wikidata_pid, schema_org) VALUES
  -- Organization-Ast (gilt via Vererbung auch für Unternehmen)
  ('lei',                'LEI',                'Organization', NULL,           'string',   NULL,           '1:1', true,  'P1278', NULL),
  ('hauptsitz_in',       'Hauptsitz in',       'Organization', NULL,           'entity',   'Ort',          '1:1', false, 'P159',  NULL),
  ('mutterorganisation', 'Mutterorganisation', 'Organization', NULL,           'entity',   'Organization', '1:n', false, 'P749',  'parentOrganization'),
  ('geführt_von',        'geführt von',        'Organization', NULL,           'entity',   'Person',       '1:n', false, 'P1037', NULL),
  ('branche',            'Branche',            'Unternehmen',  NULL,           'string',   NULL,           '1:n', false, 'P452',  NULL),
  -- Person/Agent ↔ Organization (Cross-Domain: Social-Ast trifft Finance-Ast)
  ('arbeitet_bei',       'arbeitet bei',       'Person',       NULL,           'entity',   'Organization', 'n:m', false, 'P108',  'worksFor'),
  ('beteiligt_an',       'beteiligt an',       'Agent',        NULL,           'entity',   'Unternehmen',  'n:m', false, 'P1830', NULL),
  ('rolle',              'Rolle',              'Agent',        NULL,           'string',   NULL,           '1:n', false, 'P2868', 'roleName'),
  ('anteil_prozent',     'Anteil (%)',         NULL,           'Quantifiable', 'number',   NULL,           '1:1', false, 'P1107', NULL),
  -- Wertpapier
  ('isin',               'ISIN',               'Wertpapier',   NULL,           'string',   NULL,           '1:1', true,  'P946',  NULL),
  ('ticker',             'Ticker',             'Wertpapier',   NULL,           'string',   NULL,           '1:n', false, 'P249',  'tickerSymbol'),
  ('gattung',            'Gattung',            'Wertpapier',   NULL,           'string',   NULL,           '1:1', false, NULL,    NULL),
  ('emittiert_von',      'emittiert von',      'Wertpapier',   NULL,           'entity',   'Organization', '1:1', false, NULL,    NULL),
  ('notiert_an',         'notiert an',         'Wertpapier',   NULL,           'entity',   'Organization', 'n:m', false, 'P414',  NULL),
  -- Übernahme (n-äre Rollen)
  ('käufer',             'Käufer',             'Übernahme',    NULL,           'entity',   'Agent',        '1:n', false, NULL,    NULL),
  ('übernahmeziel',      'Übernahmeziel',      'Übernahme',    NULL,           'entity',   'Unternehmen',  '1:1', false, NULL,    NULL),
  ('kaufpreis',          'Kaufpreis',          'Übernahme',    NULL,           'quantity', NULL,           '1:1', false, NULL,    NULL);

-- (d) label_predicate — nach den Prädikat-Inserts (FK). Alle drei sind Nameable
--     (direkt bzw. geerbt), der Anzeige-Bezeichner ist das name-Statement.
UPDATE entity_type SET label_predicate = 'name'
 WHERE id IN ('Unternehmen', 'Wertpapier', 'Übernahme');

-- (e) wikidata_qid: Domain anheben Ort → Nameable (s. Rationale (5)).
UPDATE predicate SET domain_type = NULL, domain_interface = 'Nameable'
 WHERE id = 'wikidata_qid';
