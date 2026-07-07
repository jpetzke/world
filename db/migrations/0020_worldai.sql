-- WorldAI: Chat-Sessions des Agenten-UIs (/ai).
--
-- Rationale (§14): Das ist reine App-Schicht wie api_key — KEINE zweite
-- Wahrheit neben dem Substrat (Invariante 1 unberührt). Chats sind
-- Konversations-Log, keine Fakten; Fakten entstehen ausschließlich über die
-- Schreib-Tools (commit_statement & Co.), die WorldAI durch dieselbe
-- Registry-/Verfassungs-Schranke ruft wie der MCP-Server.
-- ai_result ist der Result-Store fürs Offloading großer Tool-Ergebnisse:
-- jederzeit löschbar, jederzeit neu berechenbar (ableitbar markiert).

CREATE TABLE ai_session (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title       text,
  model       text,                          -- Override pro Chat; NULL = Env-Default
  pending     jsonb,                         -- Schreib-Gate: wartender Tool-Call (UI-State)
  anchors     jsonb NOT NULL DEFAULT '[]',   -- Anker-Cache: aufgelöste Entities
  anchors_sent int  NOT NULL DEFAULT 0,      -- wie viele Anker schon als Block in der History
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_message (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES ai_session(id) ON DELETE CASCADE,
  seq        int  NOT NULL,
  -- OpenAI-Chat-Format (role, content, tool_calls, tool_call_id) + "_ui"-Meta
  -- (Dauer, Digest, Anker-Flag); "_ui" wird vor dem LLM-Call gestrippt.
  payload    jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, seq)
);

CREATE TABLE ai_result (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   uuid NOT NULL REFERENCES ai_session(id) ON DELETE CASCADE,
  tool_call_id text,
  content      jsonb NOT NULL,               -- volles Tool-Ergebnis
  summary      text  NOT NULL,               -- Kurzbeschreibung (Anzahl, Struktur)
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ai_message_session_idx ON ai_message (session_id, seq);
CREATE INDEX ai_result_session_idx ON ai_result (session_id);
