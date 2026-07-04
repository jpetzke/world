-- API-Keys für externen Maschinenzugriff (App-Schicht, KEIN Substrat — wie 0008):
-- Automationen (n8n u. a.) sprechen dieselbe /api-Fläche wie die UI, aber mit
-- Key statt Session-Cookie. Drei hierarchische Scopes (read < write < admin):
--   read   Abfragen (Entities, Suche, Traversierung, Registry/Quellen lesen)
--   write  + Substrat-Writes (Statements, Entities, Sources, Ingest, Merge)
--   admin  + Vokabular/Gate (Proposals anlegen, approve/reject) — alles.
-- Ein Session-Login behält Vollzugriff; die Key-Verwaltung selbst bleibt
-- sessiongebunden (ein Key kann keine Keys erzeugen oder rotieren).
--
-- secret liegt bewusst im Klartext: Keys sind in der UI jederzeit wieder
-- anzeigbar (bewusste Produktentscheidung, privater Single-User-Betrieb).
-- Rotation ersetzt nur das Secret — Identität (id, name, scope) bleibt.

CREATE TABLE api_key (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text NOT NULL,
  secret       text NOT NULL UNIQUE,
  scope        text NOT NULL CHECK (scope IN ('read', 'write', 'admin')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  rotated_at   timestamptz,
  last_used_at timestamptz
);
