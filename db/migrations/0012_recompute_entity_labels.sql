-- Backfill: den denormalisierten entity.label-Cache aus dem aktuell besten
-- label_predicate-Statement neu ableiten (Invariante 1: label ist ableitbar,
-- jederzeit neu berechenbar — keine zweite Wahrheit).
--
-- Warum nötig: Der Write-Path setzte den Cache bislang nur bei create_entity und
-- berechnete ihn nach commit/supersede/set_rank/deprecate NIE neu. Folge: ein
-- korrigierter Bezeichner (z. B. name mit rank=preferred auf "Jonas Petzke")
-- schlug in der Source of Truth durch (welt_timeline sah den Wechsel), aber der
-- Anzeige-Cache hing auf dem alten Wert ("Jonas"). refresh_entity_label schließt
-- die Lücke ab jetzt automatisch; diese Migration heilt die bereits stale Zeilen
-- einmalig. Kein DDL, kein Registry-Write — reines Neuberechnen (§14.5 erlaubt
-- reproduzierbare Daten-Migrationen). Idempotent: mehrfaches Ausführen ist ein No-op.

UPDATE entity e
SET label = best.value_text
FROM (
  SELECT DISTINCT ON (s.subject_id) s.subject_id, s.value_text
  FROM statement s
  JOIN entity en       ON en.id = s.subject_id
  JOIN entity_type t   ON t.id = en.type_id
  WHERE t.label_predicate IS NOT NULL
    AND s.predicate_id = t.label_predicate
    AND s.system_to IS NULL
    AND s.rank <> 'deprecated'
    AND s.value_text IS NOT NULL
  ORDER BY s.subject_id,
           CASE s.rank WHEN 'preferred' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
           s.confidence DESC, s.system_from DESC
) AS best
WHERE e.id = best.subject_id
  AND e.label IS DISTINCT FROM best.value_text;
