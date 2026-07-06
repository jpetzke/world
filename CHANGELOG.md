# Changelog

## Paket 0 — Smoke-Test-Suite

- `tests/test_smoke.py`: jedes MCP-Tool wird einmal erfolgreich gegen die
  ephemere Test-DB aufgerufen; Vollständigkeits-Guard via `tools/list`
  (neues Tool ohne Smoke-Aufruf bricht die Suite). Ausführen mit
  `uv run pytest -m smoke` (lokal und im Container, s. README).
- Bugfix dabei gefunden: `welt_merge_entities` band die IDs positional in
  `partial` → erste ID landete im `conn`-Slot, Tool crashte bei jedem
  Aufruf (gleiche Klasse wie der id-Tools-Bug aus 8c85d2f).
