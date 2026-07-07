-- Qualifier können quantity tragen (Wikidata-Praxis: z. B. P1114 „Anzahl"
-- als Qualifier an einem Statement).
--
-- Rationale (Entscheidungsbaum §14.1): eine Stückzahl/Menge AN einer
-- Beziehung („hält 100 Aktien") ist Verfeinerung eines Statements —
-- Qualifier-Job, keine eigene Entity und kein eigenes Statement. Der
-- Qualifier-Store konnte quantity bisher nicht aufnehmen, weil die
-- value_unit-Spalte fehlte; die Whitelist im Write-Path (statements.py)
-- lehnte den value_type deshalb ab. Spalte nachrüsten — die Whitelist-
-- Erweiterung lebt im Code (gleicher Commit).
-- Idempotent via IF NOT EXISTS.

ALTER TABLE qualifier ADD COLUMN IF NOT EXISTS value_unit text;
