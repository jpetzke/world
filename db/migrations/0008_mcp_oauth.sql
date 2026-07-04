-- MCP-OAuth-Infrastruktur (App-Schicht, KEIN Substrat — Rationale §14.5-analog):
-- Der MCP-Server (AI-Agenten-Zugang) braucht einen eingebetteten OAuth-2.1-
-- Authorization-Server. Dessen Zustand muss Deploys überleben (In-Memory bricht
-- laufende Flows), also Postgres — dieselbe Source of Truth, aber reine
-- Betriebs-Tabellen wie schema_migrations: keine Registry-, Entity- oder
-- Statement-Strukturen, keine Invariante berührt.
--
--   mcp_oauth_client   Dynamic Client Registration (RFC 7591), Metadata als jsonb.
--   mcp_authorize_txn  parkt den /authorize-Request bis zum Login (TTL);
--                      wird NICHT beim ersten Submit konsumiert — Retries
--                      dürfen neue Codes münzen, nur Codes sind single-use.
--   mcp_token          access/refresh/code — nur SHA-256-Hashes, nie Klartext.

CREATE TABLE mcp_oauth_client (
  client_id    text PRIMARY KEY,
  registration jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mcp_authorize_txn (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id  text NOT NULL REFERENCES mcp_oauth_client(client_id) ON DELETE CASCADE,
  params     jsonb NOT NULL,   -- state, scopes, code_challenge, redirect_uri, resource
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mcp_token (
  token_hash text PRIMARY KEY,  -- sha256(token); Klartext existiert nur in der Response
  kind       text NOT NULL CHECK (kind IN ('access', 'refresh', 'code')),
  client_id  text NOT NULL REFERENCES mcp_oauth_client(client_id) ON DELETE CASCADE,
  subject    text NOT NULL,     -- Username (Single-User-Betrieb)
  scopes     text[] NOT NULL,
  data       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- Codes: PKCE-Challenge, redirect_uri
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX mcp_token_expires_idx ON mcp_token (expires_at);
