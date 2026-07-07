# Changelog

## Bug-Sweep (E2E über MCP)

- **Qualifier können quantity tragen** (Wikidata-Praxis, z. B. P1114 „Anzahl"):
  `value_unit`-Spalte im Qualifier-Store (Migration 0016), Whitelist +
  Supersession-Kopie erweitert.
- **`welt_resolve` Label-Match repariert:** `refresh_entity_label` pflegte nur
  den Label-Cache, nie das Embedding — Entities mit nachgereichtem oder
  geändertem Namen waren per Label unauffindbar. Jetzt ziehen Label UND
  Embedding nach (beide ableitbar, Invariante 1). Dazu: Typ-Filter subtypfähig
  (Agent findet Person), exakte Label-Gleichheit als Kandidat auch ohne
  Embedding (Platform-Dedup „linkedin" = „LinkedIn"), Embeddings ohne
  Typ-Prefix (typ-übergreifend vergleichbar), nicht-identifying `identifiers`
  werden als `warnings` gemeldet statt still ignoriert.
  `recompute_embeddings()` (entities.py) leitet Bestands-Embeddings neu ab —
  **nach Deploy einmal ausführen**:
  `uv run python -c "from weltmodell.db import get_conn; from weltmodell.entities import recompute_embeddings; c = get_conn(); print(recompute_embeddings(c)); c.commit()"`
- **Merge schließt Self-Loop-Artefakte:** kannte/folgte die Dublette dem
  Original, blieb nach dem Merge ein selbstreferenzielles Statement
  (d knows d) in der Current View — bewegte Zeilen mit subject = object
  werden jetzt transaktionszeitlich geschlossen (`self_loops_closed`).
- **Re-Confirm generalisiert:** exakt identische Behauptung (Wert +
  Gültigkeitsfenster, keine Qualifier) wird re-bestätigt statt dupliziert —
  ein Re-Ingest derselben Quelle erzeugte vorher bei jedem Lauf identische
  Statement-Zeilen. Label-Cache-Tiebreak jetzt: rank → confidence →
  Belegzahl → Neuheit.
- **Pipeline überlebt fehlerhafte Kandidaten:** Savepoint pro Kandidat —
  ein DB-Fehler (z. B. unparsebares LLM-Datum) landet in `rejected`, statt
  die Transaktion und alle Folge-Kandidaten abzureißen; fehlgeformte
  LLM-Items werden übersprungen und ausgewiesen statt zu crashen.
- **`welt_resolve`/`welt_search` melden unbekannte Typen** statt stumm null
  Kandidaten zu liefern (Dubletten-Falle bei Tippfehler-Typ).
- **Fehlerqualität:** rank/origin/confidence/leere Gültigkeitsfenster werden
  vor der DB validiert (klare ValidationError statt CheckViolation);
  identifying-Konflikt nennt die besitzende Entity + Kurations-Hinweis statt
  roher UniqueViolation; kaputte UUIDs/Datumsformate kommen als „Ungültige
  Eingabe: …" zurück; `propose_*` prüft kontextfreie Shape-Regeln sofort
  (fail fast); `welt_proposals` validiert `status`; `welt_stats` zählt auch
  Interface-Proposals; `welt_fix_statement` zählt sich nicht mehr selbst als
  1:1-Kardinalitätskonflikt.

## Paket 3 — Komfort

- `welt_import_snapshot`: generischer Snapshot-Import für beliebige
  n:m-Entity-Prädikate (Preview/Commit, Re-Bestätigung per Reference);
  `welt_import_follower_list` ist jetzt ein dünner Wrapper darum
  (Instagram-Spezifika: Username-Normalisierung, account_uri,
  handle/platform/name bei Neuanlage). Preview und Commit teilen sich
  jetzt die Resolve-Logik — vorher konnte die Preview-Klassifikation vom
  Commit-Verhalten abweichen (Vektor-Auto-Match galt nur im Commit).
- Interfaces proposebar: `welt_propose_interface` + Approve/Reject über
  `welt_decide_proposal` (kind=interface); Migration 0015.
- `welt_amend_proposal(proposal_id, patch)`: pending/rejected nachschärfen,
  rejected geht zurück auf pending; approved ist unveränderlich.
- Bulk-Propose: `welt_propose_types` / `welt_propose_predicates` mit
  atomic-Flag (Verhalten wie welt_create_entities).
- `min_confidence`/`rank` als Read-Filter in `welt_entity` und
  `welt_traverse` (Semantik wie `welt_query`).

## Paket 2 — Query-Fähigkeiten

- Neues Lese-Tool `welt_query`: Statement-zentrierte Suche (subject/predicate/
  object/value_text/min_confidence/rank/valid_at/system_at/limit/offset),
  Serialisierung und bitemporale Semantik identisch zu `welt_entity`
  (gemeinsame `_TIME_FILTER`-Definition). Minimale Aggregation: count/sum/avg
  (sum/avg nur number/quantity, pro unit gruppiert), group_by subject/object.
- Qualifier-Validierung festgelegt: Domain-Check für Qualifier bewusst
  ausgesetzt, range_kind wird jetzt validiert (vorher ungeprüft) —
  dokumentiert in Code und Verfassung.
- Neues Tool `welt_fix_entity(entity_id, reason)`: Erratum für versehentlich
  angelegte Anker; löscht nur ohne aktive Statements, sonst Verweis auf
  `welt_merge_entities`.

## Paket 1 — Proposal-Flow vervollständigt

- Root-Typen: `parent_id` in `welt_propose_type` optional; Approve validiert
  bei Root nur das `kind`-Etikett (Migration 0013).
- `identifying` in `welt_propose_predicate` proposebar; Approve erzwingt
  `range_kind='string'` + `cardinality='1:1'` und legt den partiellen
  Unique-Index an (Dubletten in Bestandsdaten werden berichtet, nie bereinigt).
- Migration 0014: `projekt_url` → identifying (defensiv — das Prädikat lebt
  nur in der Live-DB) + Unique-Indexe für alle identifying-Prädikate.
- `label_predicate` und `abstract` in `welt_propose_type` proposebar; Approve
  prüft Existenz + Domain-Kompatibilität; `welt_create_entity` nennt bei
  abstrakten Typen die konkreten Subtypen im Fehlertext.
- Write-Path-Ergänzung (Snapshot-Philosophie, von den neuen Indexen
  aufgedeckt): derselbe identifying-Wert auf derselben Entity wird
  re-bestätigt (neue Reference, Flag `reconfirmed`) statt dupliziert.

## Paket 0 — Smoke-Test-Suite

- `tests/test_smoke.py`: jedes MCP-Tool wird einmal erfolgreich gegen die
  ephemere Test-DB aufgerufen; Vollständigkeits-Guard via `tools/list`
  (neues Tool ohne Smoke-Aufruf bricht die Suite). Ausführen mit
  `uv run pytest -m smoke` (lokal und im Container, s. README).
- Bugfix dabei gefunden: `welt_merge_entities` band die IDs positional in
  `partial` → erste ID landete im `conn`-Slot, Tool crashte bei jedem
  Aufruf (gleiche Klasse wie der id-Tools-Bug aus 8c85d2f).
