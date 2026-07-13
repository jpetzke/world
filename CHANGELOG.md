# Changelog

## MCP-Server: Tool-Call-Log + Agent-Ergonomie

- **Tool-Call-Log** (Migration 0021): jeder MCP-Call landet mit Tool, Args
  (gekürzt), Dauer, Status, Antwortgröße und Token-Hash in `mcp_tool_log`;
  Auswertung per `welt_sql` über die Whitelist-View `v_tool_log`. Healthcheck-
  Spam (`GET /healthz`) ist aus dem uvicorn-Access-Log gefiltert.
- **Audit-Fixes für LLM-Agenten** (Befund: Log-Analyse + Tool-Audit):
  `welt_resolve` findet Teilnamen jetzt per Keyword-Fallback („Jonas" →
  „Jonas Petzke"); unbekannte Typen/Prädikate antworten mit „meintest du …?";
  `welt_query`/`welt_traverse` validieren Prädikate statt still leer zu
  liefern; `min_confidence` > 1 wird als Prozent-Verwechslung abgewiesen;
  `welt_entities` ist subtyp-fähig; Bulk-Tools überleben DB-Fehler pro Item
  (Best-Effort) und nennen den Item-Index; `welt_vocabulary` ist per Default
  kompakt (`full=true` für alles); `welt_create_source` echot `raw` nicht
  zurück; `welt_source` meldet `statements_total`; `welt_create_entity` warnt
  bei exaktem Label-Duplikat; Entity-Qualifier werden durch die Merge-Kette
  kanonisiert.

## WorldAI — agentischer Chat über dem Knowledge Graph (/ai)

Web-UI unter `/ai` (gleiche Domain, gleiches Deployment, kein neuer
Container). Beantwortet Analyse-Fragen („wer folgt A und B", „über wen
kennen sich X und Y") mit sichtbaren Zwischenschritten.

- **In-Process-Tools:** WorldAI zieht die Tool-Schemas zur Laufzeit aus
  derselben FastMCP-Registry wie der MCP-Server (`ai/tools.py`) — neue
  Server-Tools sind automatisch sichtbar, keine hartkodierten Definitionen.
  Ausführung setzt einen synthetischen Access-Token pro Chat-Session in die
  Auth-Contextvar; der Verfassungs-Session-Lock gilt unverändert.
- **Agent-Loop** (`ai/agent.py`): eigener Loop über das OpenAI-kompatible
  Chat-Completions-Format, max. 20 Iterationen, alles per SSE ans UI
  (Tokens, Tool-Start, Ergebnis-Digest, Fehler). Provider-Abstraktion
  (`ai/providers.py`): OpenRouter (Default) + Azure OpenAI, Auswahl per
  `MODEL_PROVIDER`/`MODEL_ID`, Modell-Override pro Chat im UI-Dropdown
  (`WORLDAI_MODELS`).
- **Result-Offloading** (Migration 0020, `ai/results.py`): Tool-Ergebnisse
  über `WORLDAI_RESULT_THRESHOLD` (Default 8000 Zeichen) landen im
  Session-Result-Store statt im Kontext; das Modell sieht Summary + Sample +
  `ref:<id>`. Lokales Tool **compute(code, refs)** (`ai/compute.py`,
  quickjs-Sandbox: kein Netz/FS, 3s, 128MB) verrechnet refs exakt
  (Schnittmengen, Joins) statt Mengen zu schätzen. Dockerfile: gcc für den
  quickjs-sdist-Build (multi-arch).
- **Schreib-Gate:** Schreib-Tools (Präfix-Klassifikation) pausieren den
  Loop; das UI rendert eine Bestätigungskarte mit vollen Parametern.
  Ablehnung geht als Tool-Result ans Modell zurück.
- **Sessions** in PostgreSQL (ai_session/ai_message/ai_result),
  wiederaufnehmbar; Anker-Cache aufgelöster Entities als append-only
  Delta-Block (KV-Cache-freundlich, statischer System-Prefix).
- **UI:** Chat mit SSE-Streaming, kollabierbare Step-Karten (Name,
  Parameter, Dauer, Digest), `welt_path`/`welt_traverse` zusätzlich als
  d3-force-Mini-Graph, Entity-Chips (`[[entity:<id>|<label>]]`) klickbar
  zur Entity-Seite, Session-Liste + Modell-Dropdown.

## Frontend-Redesign „Orbital" + Degree-of-Interest-Graph

Verbindliche Spec: `weltmodell-design-system.html` (Repo-Root). Der Graph
rendert nie mehr den ganzen Bestand, sondern ein repräsentatives Skeleton
plus fokusgetriebene Expansion — Node-Budget konstant ~3k, egal ob die DB
100 oder 500k Entities hält.

- **Backend (Migration 0018):** `graph_metrics` als ableitbarer Cache
  (Leiden-Community, PageRank, `community_rank`, Grad, x/y). Endpoints:
  `GET /api/graph/skeleton` (Top-PageRank je Community + globale Hubs, mit
  persistierten Positionen), `POST /api/graph/positions` (Layout-Persistenz),
  `POST /api/graph/path` (BFS-Pfad Suchtreffer → geladener Ausschnitt),
  `POST /api/graph/metrics/recompute` (admin; sonst lazy nach 24h). Neue
  Dep: python-igraph (aarch64-Wheels vorhanden).
- **Graph-Engine neu:** sigma.js 3 (WebGL) + graphology ersetzt Cytoscape;
  d3-force läuft im Web Worker (Main Thread simuliert nie). Ghost-Badges
  „+N" zeigen nicht geladene Nachbarn, Klick expandiert am Anker (Fade-in);
  Eviction LRU×Grad (Skeleton/Selektion nie). Spec-Regeln R1–R8 portiert:
  Label-LOD (Grad×Zoom + Kollisionsgrid), Kantenlabel-Chips horizontal nur
  bei Fokus, Fokus-Dimming 12 % (premultiplied — sigma blendet
  ONE/ONE_MINUS_SRC_ALPHA), Raute=Occurrent (Custom-WebGL-Program),
  Orphan-Gravitation + Auto-Fit, 20px-Treffradius (geometrisches Picking
  ersetzt sigmas gl.readPixels-Picking — das stallte die GPU-Pipeline).
- **Drag-Bug behoben:** Kanten bleiben beim Ziehen sichtbar und ziehen in
  Echtzeit mit (Ursache war Cytoscapes `hideEdgesOnViewport`; strukturell
  erledigt, mid-drag per Screenshot verifiziert).
- **Design-System global:** Orbital-Tokens in `theme.css` (Text-Trias AA,
  6 Typ-Hues, Spacing/Radius/Motion, `--hit` 40/44px), drei Font-Rollen
  (Chakra Petch/Plex Sans/Plex Mono), Buttons/Toggle-Chips/Segmented/
  Combobox ersetzen alle nativen Selects+Checkbox-Toggles, globaler
  Focus-Ring, `prefers-reduced-motion`.
- **Screens nach Audit:** Inspector als kv-Rows mit echten Buttons (geteilt,
  ≤720px Bottom-Sheet), Anlegen in 2 betitelte Gruppen (meistgenutzte Typen
  zuerst), Create-Form zweispaltig mit Live-Preview + ⓘ-Capabilities,
  Suche mit relativen Timestamps/Bulk-Gruppierung/klickbaren Stat-Cards,
  Stat-Cards in Graph-Toolbars. Mobile: Bottom-Nav, scrollende Toolbars.
- **Verifiziert** (Bench-DB 100k Nodes/299k Kanten via
  `tests/bench/seed_graph.py`, Prod-Build, GPU): Skeleton-Load 325 ms
  (<500), Expansion 118–183 ms (<200), 60 FPS ohne Long Tasks >50 ms bei
  Pan/Zoom/Drag/Simulation. Touch-Audit 0 Verstöße (Desktop 40px, Mobile
  44px) — `frontend/e2e/{touch-audit,perf,drag,shots}.mjs` (Playwright
  braucht GPU-Flags, SwiftShader verfälscht sonst die Messung).

## Person: vorname/nachname

- Neue Prädikate `vorname` (P735, givenName) und `nachname` (P734,
  familyName) auf Person (Migration 0017): string, 1:1, nicht identifying.
  Mehrere Vornamen gehen ZUSAMMEN in EIN `vorname`-Statement („Hans Peter").
  `name` bleibt der Voll-Bezeichner (label_predicate); bestehende
  name-Statements werden nicht automatisch gesplittet (Kurations-Arbeit).

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
