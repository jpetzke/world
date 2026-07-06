-- Proposal-Flow vervollständigen: Root-Typen, identifying, label_predicate,
-- abstract — alles über das Review-Gate proposebar (Invariante 2: kein Write
-- am Vokabular vorbei; das Gate muss also alles ausdrücken können, was eine
-- kuratierte Migration kann).
--
-- Rationale:
-- (1) parent_id nullable: Wertpapier (0011) zeigte den legitimen Fall eines
--     Continuant-Wurzeltyps — bislang ging das nur per Migration. Approve
--     validiert bei Root nur das kind-Etikett (Continuant/Occurrent-Split,
--     Invariante 5); bei gesetztem Parent bleibt der Kind-Match-Check.
-- (2) identifying: jeder aus Quellen befüllte Typ braucht ≥1 identifying-
--     Prädikat (§14.4) — der Extraktor-Flywheel kann den Dedup-Key bisher
--     nicht mitliefern. Approve erzwingt range_kind='string' + cardinality
--     '1:1' (Stufe-1-Resolve matcht exakt auf value_text).
-- (3) label_predicate/abstract: Typ-Proposals konnten den Anzeige-Bezeichner
--     (0005) und das abstract-Flag (0004) nicht setzen — beide sind Teil der
--     Typ-Checkliste §14.4 und gehören damit ins Gate.
-- Kein Domänen-Schema: nur Registry-/Gate-Infrastruktur (§14.5).

ALTER TABLE proposed_type ALTER COLUMN parent_id DROP NOT NULL;
ALTER TABLE proposed_type ADD COLUMN label_predicate text;
ALTER TABLE proposed_type ADD COLUMN abstract boolean NOT NULL DEFAULT false;

ALTER TABLE proposed_predicate ADD COLUMN identifying boolean NOT NULL DEFAULT false;
