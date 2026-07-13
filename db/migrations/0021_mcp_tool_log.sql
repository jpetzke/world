-- MCP-Tool-Call-Log: eine Zeile pro Tool-Aufruf für Nutzungs-Analyse.
--
-- Rationale (Entscheidungsbaum §14.1: gar kein Weltmodell-Inhalt):
-- Das ist operative Telemetrie ÜBER den Server selbst — keine Fakten über
-- die Welt, darum weder Statement noch Entity (keine Provenance, kein Rank,
-- keine Bitemporalität). Eigene Tabelle neben dem Substrat, analog
-- graph_metrics: Invariante 1 bleibt unberührt, das Substrat kennt diese
-- Tabelle nicht. Zweck: sichtbar machen, welche Tools AI-Agenten wie oft,
-- wie lange und mit wie großen Antworten aufrufen (das uvicorn-Access-Log
-- zeigt nur "POST /mcp" ohne Tool-Name/Dauer/Größe).
--
-- v_tool_log ist die welt_sql-Whitelist-Sicht darauf: Agenten können ihre
-- eigene Nutzung per SQL analysieren (Spec: docs/superpowers/specs/
-- 2026-07-13-mcp-tool-log-design.md).

CREATE TABLE IF NOT EXISTS mcp_tool_log (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  tool         text NOT NULL,
  -- Tool-Argumente; serialisiert >2KB → {"_truncated": true, <keys>: "…"}
  args         jsonb,
  duration_ms  integer NOT NULL,
  status       text NOT NULL CHECK (status IN ('ok', 'error')),
  error        text,
  -- Länge der JSON-Antwort in Bytes — Proxy für Token-Kosten beim Client
  result_bytes integer,
  -- sha256(access_token)[:12], wie der Verfassungs-Ack: korreliert Calls
  -- einer Agenten-Sitzung, ohne das Token zu leaken
  token_hash   text
);

CREATE INDEX IF NOT EXISTS mcp_tool_log_ts_idx ON mcp_tool_log (ts);
CREATE INDEX IF NOT EXISTS mcp_tool_log_tool_idx ON mcp_tool_log (tool);

CREATE OR REPLACE VIEW v_tool_log AS SELECT * FROM mcp_tool_log;
