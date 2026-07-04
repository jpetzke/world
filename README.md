# Weltmodell

Privates, unendlich erweiterbares Substrat für Entities und Events aus beliebigen
Domänen — als **reifizierter Statement-Store** auf genau einer Source of Truth:
PostgreSQL (pgvector + PostGIS). Architektur: [weltmodell-architektur.md](weltmodell-architektur.md).

## Quickstart

```bash
# 1. Datenbank (Podman, Port 5433)
podman build -t weltmodell-db:latest db/
podman run -d --name weltmodell-db \
  -e POSTGRES_USER=weltmodell -e POSTGRES_PASSWORD=weltmodell -e POSTGRES_DB=weltmodell \
  -p 5433:5432 -v weltmodell-pgdata:/var/lib/postgresql/data \
  localhost/weltmodell-db:latest

# 2. Frontend bauen (einmalig bzw. nach UI-Änderungen)
cd frontend && npm install && npm run build && cd ..

# 3. App (Migrationen laufen beim Start automatisch)
uv sync
uv run uvicorn weltmodell.api:app --port 8100
#   → UI:  http://localhost:8100/        API-Docs: /docs

# 4. Tests
uv run pytest                    # Backend (legt weltmodell_test an)
cd frontend && npm test          # Frontend (vitest)
```

Frontend-Dev mit Hot-Reload: `cd frontend && npm run dev` → http://localhost:5174
(Proxy auf die API unter :8100).

Konfiguration über `.env` (gitignored) / Umgebung:

| Variable | Default | Zweck |
|---|---|---|
| `WELTMODELL_DSN` | `postgresql://weltmodell:weltmodell@localhost:5433/weltmodell` | Source of Truth |
| `OPENROUTER_API_KEY` | — | LLM-Extraktor (optional) |
| `WELTMODELL_LLM_MODEL` | `poolside/laguna-xs-2.1:free` | OpenRouter-Modell |

## Was implementiert ist (Phasen 0–2 + §10-Beweis)

- **Registry (Schema-als-Daten, §2)** — `entity_type` (hierarchisch, Continuant/Occurrent),
  `interface`, `predicate` (domain/range/cardinality/inverse/identifying). Neuer Typ = ein
  INSERT durchs Gate, keine Migration.
- **Review-Gate (§7.1)** — `proposed_type`/`proposed_predicate` → approve/reject. Approve
  erzwingt die Registry-Regeln im Code. Gilt für Menschen- und LLM-Writes gleichermaßen.
- **Statements (§3/§4)** — reifizierte Tripel mit Qualifiern, Rank, Confidence,
  Bitemporalität (valid/system time) und Pflicht-Provenance (≥1 `reference` →
  `source_document`). Änderungen superseden bitemporal — nie Overwrite (Invariante 4).
- **Entity-Resolution (§7.2)** — deterministisch über `identifying`-Prädikate
  (email, wikidata_qid, account_uri), fuzzy über pgvector; `merge_entity` verlustfrei.
- **Pipeline (§7)** — INGEST → EXTRACT → RESOLVE → VALIDATE → COMMIT, jede Stufe mit
  Provenance. Extraktoren: regelbasiert (Demo) und LLM via OpenRouter (`weltmodell/llm.py`),
  beide constrained aufs Registry-Vokabular; Unbekanntes wird Proposal, nie Write.
- **Queries** — Current View + bitemporale Sichten (`system_at`/`valid_at`), Multi-Hop-
  Traversierung per Recursive CTE (Cross-Domain-Beweis §10 in `tests/test_queries.py`),
  semantische Suche.

Embeddings: deterministischer Hashing-Embedder als austauschbarer Default
(`weltmodell/embeddings.py`) — ableitbar und jederzeit durch ein echtes Modell ersetzbar
(Invariante 1).

## Frontend (`frontend/`)

React + Vite + TS, dunkles „Nachtarchiv"-Theme: Farbe kodiert die Ontologie
(Continuants cyan/●, Occurrents amber/◆), jede Statement-Zeile trägt links eine
Konfidenz-Kante. Views: Suche/Dashboard, Entity (inkl. Zeitreise über beide
§4-Achsen, Deprecate/Rank/Merge), Anlegen (Live-Dedup, domain-gefilterte
Prädikate, polymorpher Wert-Editor, Provenance-Pflicht), Graph (Cytoscape),
Registry, Gate, Quellen + manueller Ingest. Produktion: FastAPI liefert
`frontend/dist` auf `/` aus.

## API (FastAPI, erzwungener Schreibweg, Prefix `/api`)

| Endpoint | Zweck |
|---|---|
| `POST /entities`, `GET /entities/{id}` | Anker anlegen; Current View (`?system_at=`, `?valid_at=`, `?include_deprecated=`) |
| `POST /statements`, `POST /statements/{id}/deprecate`, `.../rank` | Commit mit Shape-Check; bitemporales Deprecate/Rank |
| `POST /sources`, `POST /ingest` | Provenance; Pipeline-Lauf (`"extractor": "rule-based"\|"llm"`) |
| `POST /resolve`, `POST /entities/{id}/merge` | Dedup-Stufen; verlustfreier Merge |
| `GET /registry/...`, `POST /registry/proposals/...` | Vokabular lesen; Gate (propose/approve/reject) |
| `GET /search`, `POST /query/traverse` | pgvector-Suche; Multi-Hop-Traverse |
| `GET/POST /keys`, `POST /keys/{id}/rotate`, `DELETE /keys/{id}` | API-Key-Verwaltung (nur Session, UI-Seite „API-Keys") |

**Zugriff:** Session-Login (UI) hat Vollzugriff. Externe Automationen (n8n & Co.)
authentifizieren per `Authorization: Bearer <key>` oder `X-API-Key`-Header;
Keys entstehen in der UI und tragen einen hierarchischen Scope —
`read` (Abfragen) < `write` (+ Substrat-Writes) < `admin` (+ Gate/Vokabular, alles).

## Bewusst später (§13, kein Redesign nötig)

OWL-Reasoning (`origin='inferred'` ist vorbereitet), Apache AGE, Auto-Approve-Gate,
materialisierte Current-View.
