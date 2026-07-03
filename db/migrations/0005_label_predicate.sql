-- Typ-abhängiges Primär-/Label-Prädikat: welches Prädikat trägt den
-- Anzeige-Bezeichner eines Typs. Für Person 'name', für SocialMediaAccount
-- der 'handle' (Username) statt eines generischen Namens. So ist der
-- Primär-Bezeichner ein echtes Statement (SoT), nicht nur der label-Cache.

ALTER TABLE entity_type ADD COLUMN label_predicate text REFERENCES predicate(id);

UPDATE entity_type SET label_predicate = 'name'   WHERE id IN ('Person', 'Organization', 'Platform');
UPDATE entity_type SET label_predicate = 'handle' WHERE id = 'SocialMediaAccount';
-- Agent bleibt NULL (abstrakt, nicht anlegbar).
