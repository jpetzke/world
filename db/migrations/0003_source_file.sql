-- Original-Datei-Ablage (Spec §5, Erweiterung): das hochgeladene Original
-- eines Dokuments landet binär in der DB, 1:1 zum source_document.
-- Bewusst kleine Dateien (< 5 MB); bytea ist dafür unproblematisch. Binär
-- liegt in eigener Tabelle, damit SELECT * auf source_document schlank bleibt.

CREATE TABLE source_file (
  source_id  uuid PRIMARY KEY REFERENCES source_document(id) ON DELETE CASCADE,
  filename   text NOT NULL,
  mime       text NOT NULL,
  size_bytes integer NOT NULL,
  sha256     text NOT NULL,           -- Integritäts-/Dedup-Prüfsumme
  data       bytea NOT NULL,          -- das Original
  created_at timestamptz DEFAULT now()
);

CREATE INDEX source_file_sha256_idx ON source_file (sha256);
