# MCP-Tool-Call-Log — Design

**Datum:** 2026-07-13
**Problem:** AI-Agenten feuern viele MCP-Requests; es gibt null Sichtbarkeit,
welche Tools wie oft, wie teuer (Dauer, Antwortgröße) und mit welchen
Argumenten aufgerufen werden. Das Prod-Access-Log (uvicorn) zeigt pro Call nur
`POST /mcp 200 OK` — kein Tool-Name, keine Dauer, keine Größe, kein Timestamp —
und besteht zu 65 % aus `GET /healthz`-Spam.

**Ziel:** Jeder MCP-Tool-Call landet als eine Zeile in Postgres und ist per
`welt_sql` auswertbar. Healthz verschwindet aus dem Access-Log.

## 1. Datenmodell — Migration `db/migrations/0021_mcp_tool_log.sql`

```sql
CREATE TABLE mcp_tool_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    tool         text NOT NULL,
    args         jsonb,          -- gekürzt bei >2KB (Keys bleiben, Werte raus)
    duration_ms  integer NOT NULL,
    status       text NOT NULL CHECK (status IN ('ok', 'error')),
    error        text,           -- ToolError-Message bei status='error'
    result_bytes integer,        -- Länge der JSON-Antwort = Token-Proxy
    token_hash   text            -- sha256(access_token)[:12] → Session-Korrelation
);
CREATE INDEX ON mcp_tool_log (ts);
CREATE INDEX ON mcp_tool_log (tool);
CREATE VIEW v_tool_log AS SELECT * FROM mcp_tool_log;
```

Kein Bruch von Invariante 1: operative Telemetrie über den Server selbst,
keine Weltmodell-Fakten — Statements/Provenance bleiben unberührt. Keine
Retention vorerst (Volumen unkritisch, ~Tausende Zeilen/Tag max).

## 2. Einhängepunkt — `LoggedFastMCP(FastMCP)`

Ein Ort statt Decorator auf ~30 Tools: Subclass überschreibt die public
Methode `call_tool(name, arguments)`:

- Zeit messen (monotonic), Original via `super().call_tool()` aufrufen.
- `result_bytes` = `len(json.dumps(result, default=str))` des Rohergebnisses.
- `args` als jsonb; serialisiert >2 KB → `{"_truncated": true}` + Top-Level-Keys
  mit Werten ersetzt durch `"…"`.
- `token_hash` = `sha256(access_token)[:12]` (wie Verfassungs-Ack), `null` wenn
  kein Token im Kontext.
- Exceptions (`ToolError` etc.): loggen mit `status='error'` + Message,
  dann re-raisen — Fehlerverhalten unverändert.
- INSERT läuft im Thread (`anyio.to_thread`, eigene Connection wie `_tx`).
  Schlägt der INSERT fehl, bricht das NIE den Tool-Call: try/except,
  Notiz auf stderr.

Erfasst damit uniform alle Tools inkl. Verfassungs-Gate-Rejects
(`_require_write` wirft im Tool-Body → status='error').

## 3. Healthz-Filter

Logging-Filter auf dem `uvicorn.access`-Logger, registriert beim App-Start in
`api.py`: Zeilen mit `GET /healthz` werden unterdrückt. Sonst unverändert.

## 4. Auswertung

`v_tool_log` kommt in die `welt_sql`-View-Whitelist (`analysis.sql_query`) und
in den `welt_sql`-Docstring. Analyse dann direkt per MCP, z. B.:

```sql
SELECT tool, count(*), avg(duration_ms)::int AS avg_ms,
       sum(result_bytes) AS bytes_total
FROM v_tool_log GROUP BY tool ORDER BY bytes_total DESC;
```

Kein eigenes Dashboard, kein neues MCP-Tool (YAGNI — welt_sql reicht).

## 5. Tests

- Tool-Call über den Test-Client schreibt Log-Zeile: korrekter `tool`-Name,
  `status='ok'`, `duration_ms >= 0`, `result_bytes > 0`, Args gespeichert.
- Fehler-Call (z. B. Schreib-Tool ohne Verfassungs-Ack) → `status='error'`,
  `error` gefüllt, Fehler erreicht weiterhin den Client.
- Args > 2 KB → `_truncated`-Marker, keine Riesen-Payload in der DB.
- `welt_sql` kann `v_tool_log` lesen (Whitelist greift).
- Healthz-Filter: `GET /healthz` erzeugt keine Access-Log-Zeile, andere
  Requests schon.

## 6. Deploy & Prod-Verify

Commit auf `main`, Push, Coolify-Deploy (Migration läuft automatisch beim
App-Start). Verify: MCP-Call gegen `https://world.jshift.de/mcp` absetzen,
danach per `welt_sql` die eigene Log-Zeile in `v_tool_log` lesen.
