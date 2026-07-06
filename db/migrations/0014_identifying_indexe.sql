-- Datenmigration: projekt_url wird identifying + DB-seitiger Dubletten-Schutz
-- für ALLE identifying-Prädikate.
--
-- Rationale:
-- (1) projekt_url ist zur Laufzeit übers Review-Gate entstanden (Live-DB,
--     nicht im Seed) und dient de facto als harter Dedup-Key — der Flag fehlt
--     nur, weil identifying bis 0013 nicht proposebar war. Defensiv: existiert
--     das Prädikat nicht (frische DB), ist das ein NOTICE-No-op; existiert es
--     mit regelwidrigem Shape (identifying erfordert range_kind='string' +
--     cardinality '1:1'), scheitert die Migration LAUT statt still zu flaggen.
-- (2) Ohne Unique-Index schützt identifying nicht vor Dubletten: resolve
--     matcht zwar deterministisch, aber zwei parallele Importe (oder ein
--     Re-Import ohne Snapshot-Semantik) könnten denselben Key zweimal
--     committen. Ein partieller Unique-Index pro identifying-Prädikat über
--     (predicate_id, value_text) auf aktuellen, nicht-deprecated Statements
--     erzwingt die Eindeutigkeit in der Source of Truth. Bestandsdaten werden
--     vorher geprüft; Konflikte werden BERICHTET (Exception mit Liste), nie
--     stumm gelöscht — Bereinigung ist Kurations-Arbeit (welt_merge_entities /
--     welt_fix_statement), kein Migrations-Nebeneffekt.
-- Idempotent: UPDATE ist wiederholbar, Indexe via IF NOT EXISTS.
-- (registry.approve_predicate legt denselben Index für neu approbierte
-- identifying-Prädikate an — Migration und Gate erfüllen dieselben Regeln.)

DO $$
DECLARE
  p record;
BEGIN
  SELECT * INTO p FROM predicate WHERE id = 'projekt_url';
  IF NOT FOUND THEN
    RAISE NOTICE 'projekt_url existiert nicht — übersprungen (frische DB)';
  ELSIF p.range_kind <> 'string' OR p.cardinality IS DISTINCT FROM '1:1' THEN
    RAISE EXCEPTION 'projekt_url kann nicht identifying werden: braucht '
      'range_kind=string + cardinality=1:1, ist range_kind=%, cardinality=%',
      p.range_kind, p.cardinality;
  ELSE
    UPDATE predicate SET identifying = true WHERE id = 'projekt_url';
  END IF;
END $$;

DO $$
DECLARE
  pred record;
  conflict record;
  msgs text := '';
BEGIN
  -- Erst prüfen, dann bauen: bestehende Duplikate laut berichten.
  FOR pred IN SELECT id FROM predicate WHERE identifying LOOP
    FOR conflict IN
      EXECUTE format(
        $q$SELECT value_text, count(*) AS n FROM statement
           WHERE predicate_id = %L AND system_to IS NULL
             AND rank <> 'deprecated' AND value_text IS NOT NULL
           GROUP BY value_text HAVING count(*) > 1$q$, pred.id)
    LOOP
      msgs := msgs || format('%s=%L (%s Statements); ',
                             pred.id, conflict.value_text, conflict.n);
    END LOOP;
  END LOOP;
  IF msgs <> '' THEN
    RAISE EXCEPTION 'identifying-Konflikte in Bestandsdaten — erst kuratieren '
      '(welt_merge_entities / welt_fix_statement), dann Migration erneut: %', msgs;
  END IF;

  FOR pred IN SELECT id FROM predicate WHERE identifying LOOP
    EXECUTE format(
      $q$CREATE UNIQUE INDEX IF NOT EXISTS %I
         ON statement (predicate_id, value_text)
         WHERE predicate_id = %L AND system_to IS NULL
           AND rank <> 'deprecated'$q$,
      'statement_ident_' || pred.id || '_uniq', pred.id);
  END LOOP;
END $$;
