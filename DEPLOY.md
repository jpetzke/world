# Deployment — Coolify (world.jshift.de)

App + Postgres (PostGIS + pgvector) laufen als **ein** `docker-compose.yml`.
TLS macht Coolifys Traefik; die App läuft intern HTTP hinter dem Proxy.
Migrationen laufen automatisch beim App-Start (idempotent, in
`schema_migrations` getrackt).

## Einmalig: Ressource anlegen

1. **Neue Ressource → Docker Compose**, als Quelle dieses Git-Repo (Branch `main`),
   Build-Pack **Docker Compose**, Compose-Pfad `docker-compose.yml`.
2. **Domain** auf den Service **`app`** setzen: `https://world.jshift.de`,
   Ziel-Port **8100**. Coolify erzeugt das Let's-Encrypt-Zertifikat und die
   Traefik-Routen selbst.
3. **Environment Variables** setzen (Coolify → Environment). Secrets nie ins Repo:

   ```
   AUTH_USERNAME=<dein-login>
   AUTH_PASSWORD=<starkes-passwort>          # openssl rand -base64 24
   SESSION_SECRET=<langes-random>            # python -c "import secrets;print(secrets.token_urlsafe(48))"
   POSTGRES_PASSWORD=<starkes-db-passwort>   # openssl rand -base64 24
   WELTMODELL_ENV=production
   # optional:
   OPENROUTER_API_KEY=<key>
   ```

   `WELTMODELL_DSN` wird im Compose aus `POSTGRES_*` gebaut — nicht selbst setzen.
   Ohne `SESSION_SECRET`/`AUTH_*`/`POSTGRES_PASSWORD` startet der Stack bewusst nicht.
4. **Deploy** klicken. Healthcheck (`/healthz`) muss grün werden; erst dann
   routet Traefik. Erststart baut das Image (npm + Python-Deps) → einige Minuten.
5. `https://world.jshift.de` öffnen → Login-Seite. Mit `AUTH_USERNAME`/`AUTH_PASSWORD` anmelden.

## Persistenz / Backup

Die DB liegt im benannten Volume `pgdata` (überlebt Redeploys). Backup via
Coolify-Scheduled-Backup auf den `db`-Service oder manuell:
`pg_dump` gegen den `db`-Container.

## Regelmäßige Updates

- **Code-Update**: nach `git push` auf `main` in Coolify **Redeploy** (oder
  Auto-Deploy-Webhook aktivieren). Neues Image wird gebaut, Container getauscht.
- **Migrationen**: neue `db/migrations/NNNN_*.sql` committen — laufen beim
  App-Start automatisch, genau einmal. Schlägt eine Migration fehl, crasht der
  App-Container (Healthcheck rot) statt still zu korrumpieren → im Coolify-Log sichtbar.
- **Rollback**: Coolify hält vorherige Deployments; bei kaputter Migration
  vorheriges Image redeployen und die Migration fixen. Migrationen sind
  additiv (nie DROP auf Fakten, Invariante 4) — Vorwärts-Fix statt Rückbau.

## Sicherheitsmodell (kurz)

- Single-User-Login, signiertes HttpOnly/Secure/SameSite=Strict-Session-Cookie.
- Brute-Force-Lockout (5 Fehlversuche/IP → 15 min). Deshalb **1 uvicorn-Worker**
  (State ist prozesslokal) — im Dockerfile-CMD fixiert.
- Security-Header: CSP (`default-src 'self'`), HSTS, `X-Frame-Options: DENY`,
  `nosniff`, `Referrer-Policy: no-referrer`.
- `/docs`/OpenAPI in Prod aus. Container-Port nur `expose` (nicht auf den Host
  gemappt) → nur Traefik erreicht die App.

## MCP-Server (AI-Agenten-Zugang)

- Endpunkt: `https://world.jshift.de/mcp` (Streamable HTTP, stateless, JSON).
  In claude.ai / Claude Code als Custom Connector mit genau dieser URL
  eintragen — OAuth-Discovery, Registrierung (DCR) und PKCE laufen automatisch.
- **`PUBLIC_URL` muss in Coolify gesetzt sein** (`https://world.jshift.de`):
  daraus entstehen Issuer-/Metadata-URLs und die erlaubten Hosts der
  DNS-Rebinding-Protection (falscher Host ⇒ 421).
- Login beim Verbinden: dieselben `AUTH_USERNAME`/`AUTH_PASSWORD` wie die Web-UI
  (eigene Seite unter `/oauth/login`, gleicher Brute-Force-Lockout).
- Tokens sind opak (`welt_at_…`), nur als SHA-256-Hash in Postgres
  (`mcp_token`); Access 4 h, Refresh 60 Tage mit Rotation. Zugriff entziehen:
  `DELETE FROM mcp_token;` (Clients holen sich per Re-Auth neue).
- Schreib-Tools sind serverseitig gesperrt, bis der Agent pro Sitzung einmal
  `welt_constitution` gelesen hat (Verfassungs-Gate, `constitution.md`).
