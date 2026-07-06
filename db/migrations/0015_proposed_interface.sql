-- Interfaces proposebar: dritter Proposal-Pfad neben Typ und Prädikat.
--
-- Rationale: Interfaces komponieren Typ-Fähigkeiten (§2.2, Nameable/Locatable/
-- Quantifiable …) und sind bislang nur per Migration erweiterbar — das Gate
-- (Invariante 2) muss aber alles ausdrücken können, was eine kuratierte
-- Migration kann (Vorbild 0013). Minimal: eine Registry-Zeile (id, label);
-- Verhaltens-Semantik trägt das Interface bewusst nicht — es wird von
-- propose_type (interfaces) und propose_predicate (domain_interface)
-- referenziert. Gleiche Gate-Mechanik: proposed_* → approve/reject.

CREATE TABLE proposed_interface (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  interface_id text NOT NULL,
  label        text NOT NULL,
  rationale    text,
  proposed_by  text NOT NULL,
  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','approved','rejected')),
  created_at   timestamptz DEFAULT now(),
  decided_at   timestamptz
);
