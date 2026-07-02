# Weltmodell-Frontend — Design (approved 2026-07-02)

## Ziel

Vollständiges Web-UI fürs Substrat: Browsen, manuelle Dateneingabe, Registry/Gate,
Suche, Graph. Automatischer Ingest (n8n) kommt später.

## Architektur

- `frontend/`: Vite + React 18 + TypeScript. Kein CSS-Framework — eigenes
  Dark-Theme (CSS Custom Properties), dicht, technisch, Monospace für IDs/Prädikate.
- TanStack Query (Cache/Invalidierung), react-router, Cytoscape.js (Graph).
- API bekommt `/api`-Prefix. Prod: FastAPI mountet `frontend/dist` auf `/` mit
  SPA-Fallback — ein Prozess auf 8100. Dev: Vite :5174, Proxy `/api` → :8100.
- Neue Backend-Endpoints: `GET /api/sources` (+`/{id}`), `GET /api/stats`,
  `GET /api/entities` (Browse, Typ-Filter, Pagination). CORS für Dev.

## Views

1. **Dashboard `/`** — semantische Suche + Typ-Filter, Stats, Pending-Proposals-Badge.
2. **Entity `/entity/:id`** — Statements gruppiert nach Prädikat (Rank, Confidence,
   Gültigkeit, Qualifier, Quellen), Incoming, Zeitreise (`system_at`/`valid_at`/
   deprecated-Toggle), Aktionen: Statement anlegen, deprecate, Rank, Merge.
3. **Graph `/graph/:id`** — Cytoscape force-directed, Tiefe-Slider, Prädikat-Filter,
   Klick = Seitenpanel, Doppelklick = Entity-Seite.
4. **Anlegen `/create`** — Entity-Formular mit Live-Dedup („Meintest du …?" via
   /resolve vor dem Speichern); Statement-Formular: Subjekt-Autocomplete →
   domain-gefilterte Prädikate → polymorpher Wert-Editor je range_kind →
   Qualifier → Quelle wählen/inline anlegen → Rank/Confidence/Gültigkeit.
5. **Registry `/registry`** — Typ-Baum (kind, Interfaces), Prädikat-Tabelle,
   Proposal-Formulare.
6. **Gate `/gate`** — pending Proposals mit Rationale, Approve/Reject, Historie.
7. **Quellen `/sources`** — Dokument-Liste + raw, manueller Ingest mit
   Extraktor-Wahl (rule-based/llm) und Pipeline-Report.

## Fehlerbehandlung

Gate-Rejects (422) sind erwartetes Verhalten → inline im Formular anzeigen
(Problems-Liste der API), nicht nur Toast.

## Testing

Backend: pytest (neue Endpoints + /api-Prefix). Frontend: Vitest für den
polymorphen Wert-Editor; `tsc` + `vite build` grün. E2E: Build von FastAPI
serviert, Browser-Smoke.
