# Changelog

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
