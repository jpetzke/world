-- Firmen können Accounts besitzen: owns_account/account_of vom Person- auf den
-- Agent-Level heben (Agent = Person ODER Organization). Der Gate-Shape-Check ist
-- subtyp-fähig (is_subtype), die Autocomplete-Suche wird subtyp-fähig gemacht.

UPDATE predicate SET domain_type = 'Agent' WHERE id = 'owns_account';
UPDATE predicate SET range_type  = 'Agent' WHERE id = 'account_of';

-- Platform ist ein Dienst, kein Akteur → aus dem Agent-Ast lösen, sonst würde
-- eine Platform als möglicher Account-Besitzer angeboten. Eigene Wurzel + Nameable.
UPDATE entity_type SET parent_id = NULL WHERE id = 'Platform';
INSERT INTO type_implements (type_id, interface_id) VALUES ('Platform', 'Nameable');
