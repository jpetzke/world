"""MCP-Server: der Agenten-Zugang zum Weltmodell.

Alle Tools laufen durch dieselben Service-Funktionen wie die API (Invariante 2:
Registry-Gate, commit_statement, merge_entity — nie rohes SQL). Zusätzlich
erzwingt ein Verfassungs-Gate den Kern des Projekts serverseitig: Schreib-Tools
sind gesperrt, bis der Agent in dieser Sitzung (= pro Access-Token) einmal
``welt_constitution`` gelesen hat. Ändert sich die Verfassung, verfällt der
Ack (Versions-Hash) — der Agent muss neu lesen.

Antworten sind token-sparsam: null-Felder und Binär-/Vektor-Spalten werden
weggelassen, Roh-Dokumente nur auf Anfrage voll ausgeliefert.
"""

import hashlib
import json
from functools import partial
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from pydantic_core import to_jsonable_python
from starlette.routing import Route

from . import follower_import, pipeline, queries, registry, resolution, statements
from .config import get_public_url, is_prod
from .db import get_conn
from .entities import create_entities, create_entity
from .errors import NotFoundError, RegistryError, ValidationError
from .mcp_auth import SCOPES, WeltOAuthProvider

_PUBLIC_URL = get_public_url()
_parsed = urlparse(_PUBLIC_URL)

_INSTRUCTIONS = """\
Weltmodell: privates, domänenübergreifendes Weltmodell als reifizierter
Statement-Store (Entities + bitemporale Statements mit Provenance) auf
PostgreSQL. Arbeitsablauf:
1. PFLICHT vor jeder Schreibaktion: einmal pro Sitzung welt_constitution
   aufrufen — Schreib-Tools sind bis dahin serverseitig gesperrt.
2. Vor dem Anlegen: welt_resolve / welt_search — Duplikate vermeiden.
3. Kein Fakt ohne Quelle: erst welt_create_source, dann welt_commit_statement.
4. Fehlendes Vokabular nie umgehen: welt_propose_type / welt_propose_predicate,
   Review mit welt_decide_proposal.
5. Mehreres auf einmal: welt_create_entities / welt_commit_statements (Bulk,
   ein Roundtrip) den Einzel-Tools vorziehen. Echten Record-Fehler in place
   korrigieren: welt_fix_statement (Erratum, kein Modellieren von Zeitverläufen).
Antworten sind kompakt: null-Felder werden weggelassen."""

_allowed_hosts = [_parsed.netloc, "localhost:*", "127.0.0.1:*"]
if not is_prod():
    _allowed_hosts.append("testserver")  # Starlette-TestClient

mcp = FastMCP(
    name="weltmodell",
    instructions=_INSTRUCTIONS,
    auth_server_provider=WeltOAuthProvider(),
    auth=AuthSettings(
        issuer_url=_PUBLIC_URL,
        resource_server_url=f"{_PUBLIC_URL}/mcp",
        required_scopes=["welt:read"],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=SCOPES,
            # Clients (claude.ai) registrieren teils ohne Scope — ohne Defaults
            # trügen ihre Tokens keine Scopes → 403 insufficient_scope.
            default_scopes=SCOPES,
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=[
            f"{_parsed.scheme}://{_parsed.netloc}",
            "https://claude.ai", "https://claude.com",
            "http://localhost:*", "http://127.0.0.1:*",
        ],
    ),
    streamable_http_path="/mcp",
    # Stateful Sessions sterben bei jedem Deploy und brechen die
    # Auth-Contextvar-Propagation — stateless + JSON ist die robuste Wahl.
    stateless_http=True,
    json_response=True,
)


# --- Verfassungs-Gate ---------------------------------------------------------

_CONSTITUTION_FILE = Path(__file__).with_name("constitution.md")
_constitution_cache: tuple[str, str] | None = None
# Ack pro Access-Token (In-Memory, 1 Worker): sha256(token) → Verfassungs-Version.
_constitution_acks: dict[str, str] = {}


def _constitution() -> tuple[str, str]:
    global _constitution_cache
    if _constitution_cache is None:
        text = _CONSTITUTION_FILE.read_text()
        _constitution_cache = (text, hashlib.sha256(text.encode()).hexdigest()[:12])
    return _constitution_cache


def _token() -> AccessToken:
    token = get_access_token()
    if token is None:
        raise ToolError("Nicht authentifiziert.")
    return token


def _require_write() -> None:
    token = _token()
    if "welt:write" not in token.scopes:
        raise ToolError("Token hat keinen welt:write-Scope — Schreibzugriff verweigert.")
    _, version = _constitution()
    key = hashlib.sha256(token.token.encode()).hexdigest()
    if _constitution_acks.get(key) != version:
        raise ToolError(
            "Schreibaktion gesperrt: Rufe zuerst welt_constitution auf und richte "
            "dich nach der Verfassung (Pflicht einmal pro Sitzung; nach einer "
            "Verfassungs-Änderung erneut)."
        )


# --- Ausführung + kompakte Serialisierung -------------------------------------

# value_geo ist binäres PostGIS-Format (Sichten liefern value_geojson),
# embedding wäre ein nutzloser 100+-Float-Dump.
_STRIP_KEYS = {"embedding", "value_geo"}


def _compact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _compact(v)
            for k, v in obj.items()
            if v is not None and k not in _STRIP_KEYS
        }
    if isinstance(obj, list):
        return [_compact(x) for x in obj]
    return obj


def _tx(fn):
    conn = get_conn()
    try:
        result = fn(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _run(fn) -> Any:
    """Tool-Body im Thread (FastMCP führt sync Funktionen sonst AUF dem
    Event-Loop aus — ein langsamer DB-Call fröre alle Requests ein)."""
    try:
        result = await anyio.to_thread.run_sync(partial(_tx, fn))
    except ValidationError as exc:
        problems = "; ".join(exc.problems) if exc.problems else str(exc)
        raise ToolError(f"Validierung fehlgeschlagen: {problems}")
    except RegistryError as exc:
        raise ToolError(f"Registry: {exc}")
    except NotFoundError as exc:
        raise ToolError(str(exc))
    return _compact(to_jsonable_python(result))


# --- Verfassung ----------------------------------------------------------------


@mcp.tool()
async def welt_constitution() -> dict[str, Any]:
    """Verfassung des Weltmodells lesen (Invarianten + Modellierungsregeln).

    PFLICHT vor jeder Schreibaktion: Schreib-Tools sind serverseitig gesperrt,
    bis dieses Tool in der aktuellen Sitzung aufgerufen wurde. Einmal pro
    Sitzung reicht; nach einer Verfassungs-Änderung erneut nötig."""
    token = _token()
    text, version = _constitution()
    _constitution_acks[hashlib.sha256(token.token.encode()).hexdigest()] = version
    return {"version": version, "text": text}


@mcp.resource("welt://constitution", mime_type="text/markdown")
def constitution_resource() -> str:
    """Verfassung des Weltmodells (Invarianten + Modellierungsregeln).

    Hinweis: Die Schreib-Freischaltung erfolgt nur über das Tool
    welt_constitution — Resources können den Ack nicht setzen."""
    return _constitution()[0]


# --- Lesen / Entdecken ----------------------------------------------------------


@mcp.tool()
async def welt_stats() -> dict[str, Any]:
    """Überblick: Anzahl Entities, Statements, Quellen, offene Proposals
    und Entities pro Typ. Guter erster Call zum Orientieren."""
    return await _run(queries.stats)


@mcp.tool()
async def welt_vocabulary(
    part: Literal["all", "types", "predicates", "interfaces"] = "all",
) -> dict[str, Any]:
    """Registry-Vokabular: erlaubte Typen (mit Parent/kind), Prädikate (mit
    Domain, Range, Cardinality, identifying) und Interfaces. Nur dieses
    Vokabular darf in Statements verwendet werden — fehlt etwas, Proposal
    einreichen statt improvisieren."""

    def q(conn):
        vocab = registry.vocabulary(conn)
        if part != "all":
            return {part: vocab[part]}
        return vocab

    return await _run(q)


@mcp.tool()
async def welt_search(q: str, type_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Semantische Suche über Entity-Embeddings + Label-Substring-Fallback.
    type_id filtert subtyp-fähig (Agent findet auch Person/Organization).
    Liefert id, label, type_id, similarity."""
    return await _run(
        partial(queries.semantic_search, query=q, type_id=type_id,
                limit=min(limit, 50))
    )


@mcp.tool()
async def welt_resolve(
    type_id: str,
    label: str | None = None,
    identifiers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Kanonischen Anker für eine Entity-Beschreibung finden — IMMER vor dem
    Anlegen aufrufen. identifiers = {prädikat_id: wert} mit identifying-
    Prädikaten (z. B. account_uri, email) matchen deterministisch; sonst
    liefert label Vektor-Kandidaten. match=null ⇒ kein sicherer Treffer."""

    def q(conn):
        result = resolution.resolve(
            conn, type_id=type_id, label=label, identifiers=identifiers or {}
        )
        # match: null ist hier Signal, kein Rauschen — explizit erhalten.
        result["match"] = result["match"] or "NONE"
        return result

    out = await _run(q)
    if out.get("match") == "NONE":
        out["match"] = None
    return out


@mcp.tool()
async def welt_entities(
    type_id: str | None = None,
    q: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Entities auflisten/filtern (type_id exakt, q als Label-Substring).
    Liefert items (id, type_id, label, statement_count) + total."""
    return await _run(
        partial(queries.list_entities, type_id=type_id, q=q,
                limit=min(limit, 200), offset=offset)
    )


@mcp.tool()
async def welt_entity(
    entity_id: str,
    system_at: str | None = None,
    valid_at: str | None = None,
    include_deprecated: bool = False,
) -> dict[str, Any]:
    """Vollsicht einer Entity: ausgehende Statements (mit Qualifiern +
    Quellen) und eingehende Statements. Zeitreisen: valid_at = „was war am
    Datum D wahr?", system_at = „was glaubte ich am Datum D?" (ISO-Datetime)."""
    return await _run(
        partial(queries.entity_view, entity_id=entity_id, system_at=system_at,
                valid_at=valid_at, include_deprecated=include_deprecated)
    )


@mcp.tool()
async def welt_timeline(entity_id: str) -> list[dict[str, Any]]:
    """Zeitleiste einer Entity: echte Ereignisse (Occurrents, die sie
    referenzieren) + abgeleitete Meilensteine (datetime-Statements,
    Label-Wechsel aus der Supersession-Historie)."""
    return await _run(partial(queries.entity_timeline, entity_id=entity_id))


@mcp.tool()
async def welt_traverse(
    start_id: str,
    max_depth: int = 1,
    predicates: list[str] | None = None,
    max_nodes: int = 60,
) -> dict[str, Any]:
    """Graph-Nachbarschaft (k-Hop, ungerichtet, zyklensicher) als Teilgraph:
    nodes (id, label, type_id, degree, depth) + edges (subject→object mit
    Prädikat). predicates schränkt auf bestimmte Kanten ein. total_nodes
    nennt die echte Größe, wenn max_nodes kappt."""
    return await _run(
        partial(queries.neighborhood, start_id=start_id, max_depth=max_depth,
                predicates=predicates, max_nodes=min(max_nodes, 500))
    )


@mcp.tool()
async def welt_sources(limit: int = 25, offset: int = 0) -> dict[str, Any]:
    """Quellen (source_documents) auflisten: id, url, activity, agent,
    retrieved_at, statement_count, ggf. Datei-Metadaten."""
    return await _run(
        partial(queries.list_sources, limit=min(limit, 200), offset=offset)
    )


@mcp.tool()
async def welt_source(source_id: str, include_raw: bool = False) -> dict[str, Any]:
    """Quelle im Detail: Metadaten, daraus belegte Statements, Datei-Info.
    raw (Original-Payload) wird ab 2000 Zeichen gekürzt — include_raw=true
    liefert alles."""

    def q(conn):
        result = queries.get_source(conn, source_id)
        raw = result["source"].get("raw")
        if raw is not None and not include_raw:
            dump = json.dumps(raw, ensure_ascii=False)
            if len(dump) > 2000:
                result["source"]["raw"] = {
                    "_truncated": True,
                    "_preview": dump[:2000],
                    "_hint": "include_raw=true für den vollen Payload",
                }
        return result

    return await _run(q)


@mcp.tool()
async def welt_proposals(status: str = "pending") -> dict[str, Any]:
    """Vokabular-Proposals auflisten (status: pending/approved/rejected).
    Review-Gate für neue Typen und Prädikate — entscheiden mit
    welt_decide_proposal."""
    return await _run(partial(registry.list_proposals, status=status))


# --- Schreiben (Verfassungs-Gate) ----------------------------------------------


@mcp.tool()
async def welt_create_source(
    activity: str,
    agent: str,
    url: str | None = None,
    raw: dict[str, Any] | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Quelle (source_document) registrieren — Voraussetzung für jedes
    Statement (Invariante 3). activity beschreibt die Herkunft (z. B.
    'scrape:instagram', 'chat:recherche'), agent den Urheber (z. B.
    'mcp:claude'). raw kann den Original-Payload archivieren."""
    _require_write()
    return await _run(
        partial(pipeline.ingest_document, raw=raw or {}, url=url,
                activity=activity, agent=agent, retrieved_at=retrieved_at)
    )


@mcp.tool()
async def welt_create_entity(
    type_id: str,
    label: str | None = None,
    embed_text: str | None = None,
) -> dict[str, Any]:
    """Entity (Identitäts-Anker) anlegen. VORHER welt_resolve/welt_search —
    Duplikate vermeiden; Dubletten später nur per welt_merge_entities heilbar.
    embed_text übersteuert den Embedding-Text (Default: label). Fakten über
    die Entity danach als Statements committen, nie hier hineinquetschen.
    Mehrere Entities auf einmal? welt_create_entities bevorzugen (ein Roundtrip)."""
    _require_write()
    return await _run(
        partial(create_entity, type_id=type_id, label=label, embed_text=embed_text)
    )


@mcp.tool()
async def welt_create_entities(
    entities: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """BULK-Variante von welt_create_entity — für mehrere Anker BEVORZUGT nutzen
    (ein Roundtrip statt N). entities: [{"type_id":…, "label"?:…, "embed_text"?:…}].
    VORHER pro Ziel welt_resolve/welt_search (Duplikate vermeiden).
    atomic=true (Default): alles-oder-nichts, der erste Fehler bricht ab.
    atomic=false: Best-Effort — gültige Anker entstehen, fehlerhafte werden im
    results-Report (index, ok, error) übersprungen. Liefert total/committed/results."""
    _require_write()
    return await _run(partial(create_entities, items=entities, atomic=atomic))


@mcp.tool()
async def welt_commit_statement(
    subject_id: str,
    predicate_id: str,
    value: dict[str, Any],
    source_ids: list[str],
    rank: str = "normal",
    confidence: float = 1.0,
    origin: str = "asserted",
    valid_from: str | None = None,
    valid_to: str | None = None,
    qualifiers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fakt committen (validiert gegen Registry: Domain/Range/Cardinality).
    value ist polymorph über value.type:
      {"type":"entity","object_id":…} · {"type":"string","text":…} ·
      {"type":"number","number":…} · {"type":"quantity","number":…,"unit":…} ·
      {"type":"datetime","datetime":ISO} · {"type":"geo","lat":…,"lon":…} ·
      {"type":"json","json":…}
    qualifiers: [{"predicate_id":…, "value":{…}}] verfeinern das Statement
    (reguläre Registry-Prädikate, dual nutzbar — z. B. beginn als Zeit-Qualifier).
    valid_from/valid_to = Gültigkeit der BEHAUPTUNG (Ereigniszeit ist ein
    eigenes beginn/ende-Statement!). Ehrliche confidence < 1.0 ist normal.
    Kardinalitätskonflikte kommen als flags zurück, kein Reject.
    Mehrere Fakten auf einmal? welt_commit_statements bevorzugen (ein Roundtrip)."""
    _require_write()
    return await _run(
        partial(statements.commit_statement, subject_id=subject_id,
                predicate_id=predicate_id, value=value, source_ids=source_ids,
                rank=rank, confidence=confidence, origin=origin,
                valid_from=valid_from, valid_to=valid_to,
                qualifiers=qualifiers or [])
    )


@mcp.tool()
async def welt_commit_statements(
    statements_batch: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """BULK-Variante von welt_commit_statement — für mehrere Fakten BEVORZUGT
    nutzen (ein Roundtrip statt N). Jedes Element trägt die Felder von
    welt_commit_statement:
      {"subject_id":…, "predicate_id":…, "value":{…}, "source_ids":[…],
       "rank"?:…, "confidence"?:…, "origin"?:…, "valid_from"?:…, "valid_to"?:…,
       "qualifiers"?:[…]}
    atomic=true (Default): alles-oder-nichts, der erste Fehler bricht ab und
    nennt den Item-Index. atomic=false: Best-Effort per Savepoint — gültige
    Fakten werden committet, fehlerhafte im results-Report (index, ok, error/
    id, flags) übersprungen. Liefert total/committed/results."""
    _require_write()
    return await _run(
        partial(statements.commit_statements, items=statements_batch, atomic=atomic)
    )


@mcp.tool()
async def welt_deprecate_statement(
    statement_id: str, valid_to: str | None = None
) -> dict[str, Any]:
    """Statement zurückziehen — supersede statt DELETE (Invariante 4): alte
    Zeile wird transaktionszeitlich geschlossen, neue Zeile mit rank=deprecated
    (Qualifier + Referenzen wandern mit). valid_to begrenzt optional zusätzlich
    die Gültigkeit der Behauptung."""
    _require_write()
    return await _run(
        partial(statements.deprecate_statement, statement_id=statement_id,
                valid_to=valid_to)
    )


@mcp.tool()
async def welt_set_rank(
    statement_id: str, rank: Literal["preferred", "normal", "deprecated"]
) -> dict[str, Any]:
    """Rank eines Statements ändern (Supersession, Historie bleibt).
    preferred markiert die beste von mehreren koexistierenden Behauptungen."""
    _require_write()
    return await _run(
        partial(statements.supersede_statement, statement_id=statement_id, rank=rank)
    )


@mcp.tool()
async def welt_fix_statement(
    statement_id: str,
    reason: str,
    delete: bool = False,
    value: dict[str, Any] | None = None,
    rank: Literal["preferred", "normal", "deprecated"] | None = None,
    confidence: float | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, Any]:
    """ERRATUM-Korrektur: überschreibt ein Statement IN PLACE oder löscht es —
    die Ausnahme von Invariante 4, NUR für echte Fehler im Record.

    Abgrenzung — erst diese Frage beantworten:
    - Hat sich die WELT geändert / gibt es eine bessere Behauptung? → NICHT fix.
      Nimm welt_commit_statement (+ welt_set_rank/welt_deprecate_statement);
      Historie muss erhalten bleiben.
    - War die Zeile schlicht FALSCH (Tippfehler, falscher Wert/Datum, versehentlich
      angelegt) und hätte so nie existieren dürfen? → fix.

    Setzt nur die übergebenen Felder (value/rank/confidence/valid_from/valid_to)
    hart neu — kein neuer Versionssatz, keine deprecated-Kopie. delete=true entfernt
    das Statement samt Qualifiern und Referenzen ganz. Wirkt auf JEDE Zeile per id,
    auch historische. value wird gegen die Registry re-validiert (ein Fix erzeugt
    nie ein ungültiges Statement). reason ist Pflicht (Audit)."""
    _require_write()
    return await _run(
        partial(statements.fix_statement, statement_id=statement_id, reason=reason,
                delete=delete, value=value, rank=rank, confidence=confidence,
                valid_from=valid_from, valid_to=valid_to)
    )


@mcp.tool()
async def welt_merge_entities(entity_id: str, target_id: str) -> dict[str, Any]:
    """Dublette verlustfrei in den kanonischen Anker mergen: entity_id wird
    auf target_id umgebogen, Provenance beider Seiten bleibt erhalten.
    Nicht umkehrbar — vorher per welt_entity beide Seiten prüfen."""
    _require_write()
    return await _run(partial(resolution.merge_entity, entity_id, target_id))


@mcp.tool()
async def welt_propose_type(
    type_id: str,
    parent_id: str,
    kind: Literal["continuant", "occurrent"],
    label: str,
    interfaces: list[str] | None = None,
    wikidata_qid: str | None = None,
    rationale: str | None = None,
    proposed_by: str = "mcp-agent",
) -> dict[str, Any]:
    """Neuen Entity-Typ vorschlagen (Review-Gate, Invariante 2). Vorher den
    Entscheidungsbaum der Verfassung durchgehen — oft ist ein Statement oder
    ein vorhandener Typ die richtige Antwort. kind muss zum Parent passen
    (Continuant/Occurrent-Split). rationale: warum dieser Ast, dieses kind."""
    _require_write()
    return await _run(
        partial(registry.propose_type, type_id=type_id, parent_id=parent_id,
                kind=kind, label=label, interfaces=interfaces or [],
                wikidata_qid=wikidata_qid, rationale=rationale,
                proposed_by=proposed_by)
    )


@mcp.tool()
async def welt_propose_predicate(
    predicate_id: str,
    label: str,
    range_kind: str,
    domain_type: str | None = None,
    domain_interface: str | None = None,
    range_type: str | None = None,
    cardinality: str | None = None,
    inverse_id: str | None = None,
    wikidata_pid: str | None = None,
    schema_org: str | None = None,
    rationale: str | None = None,
    proposed_by: str = "mcp-agent",
) -> dict[str, Any]:
    """Neues Prädikat vorschlagen (Review-Gate). Domain so hoch wie möglich
    (Typ ODER Interface), Range eng aber subtyp-offen, scharfe Rollen-Prädikate
    statt participant+Qualifier, Verfeinerung ist Qualifier-Job. wikidata_pid/
    schema_org mitgeben, wo es das extern gibt."""
    _require_write()
    return await _run(
        partial(registry.propose_predicate, predicate_id=predicate_id,
                label=label, range_kind=range_kind, domain_type=domain_type,
                domain_interface=domain_interface, range_type=range_type,
                cardinality=cardinality, inverse_id=inverse_id,
                wikidata_pid=wikidata_pid, schema_org=schema_org,
                rationale=rationale, proposed_by=proposed_by)
    )


@mcp.tool()
async def welt_decide_proposal(
    kind: Literal["type", "predicate"],
    proposal_id: str,
    decision: Literal["approve", "reject"],
) -> dict[str, Any]:
    """Proposal entscheiden. Approve prüft hart (Parent-kind-Match, Domain/
    Range existieren) und schreibt in die Registry. Vor dem Approve gegen die
    Design-Regeln der Verfassung prüfen — ein falsch approbierter Typ ist
    teuer. Bei Zweifel: reject mit neuem, besserem Proposal."""
    _require_write()

    def q(conn):
        if decision == "approve":
            if kind == "type":
                return registry.approve_type(conn, proposal_id)
            return registry.approve_predicate(conn, proposal_id)
        table = "proposed_type" if kind == "type" else "proposed_predicate"
        return registry.reject_proposal(conn, table, proposal_id)

    return await _run(q)


@mcp.tool()
async def welt_ingest(
    activity: str,
    agent: str,
    raw: dict[str, Any],
    url: str | None = None,
    retrieved_at: str | None = None,
    extractor: Literal["none", "rule-based", "llm"] = "rule-based",
) -> dict[str, Any]:
    """Rohdokument ingestieren und optional die Pipeline laufen lassen
    (EXTRACT → RESOLVE → VALIDATE → COMMIT, jede Stufe mit Provenance).
    extractor='llm' nutzt den OpenRouter-Extraktor (mappt aufs Registry-
    Vokabular oder emittiert Proposals), 'none' archiviert nur. Für Fakten,
    die du selbst schon strukturiert hast, stattdessen welt_create_source +
    welt_commit_statement verwenden."""
    _require_write()

    def q(conn):
        doc = pipeline.ingest_document(
            conn, raw=raw, url=url, activity=activity, agent=agent,
            retrieved_at=retrieved_at,
        )
        report = None
        if extractor != "none":
            ext = None
            if extractor == "llm":
                from .llm import LLMExtractor

                ext = LLMExtractor()
            report = pipeline.run_pipeline(
                conn, source_id=str(doc["id"]), agent=agent, extractor=ext
            )
        return {"source": doc, "pipeline": report}

    return await _run(q)


@mcp.tool()
async def welt_import_follower_list(
    owner_entity_id: str,
    direction: Literal["followers", "following"],
    rows: list[dict[str, Any]],
    mode: Literal["preview", "commit"] = "preview",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Follower-/Following-Liste importieren. rows: [{"username":…,
    "display_name":…?}]. IMMER erst mode='preview' (read-only, klassifiziert
    jede Row: neu/bekannt/re-bestätigt), Ergebnis prüfen, dann mode='commit'.
    Commit re-bestätigt Bekanntes per Reference statt zu duplizieren
    (Snapshot-Philosophie)."""
    if mode == "commit":
        _require_write()
        return await _run(
            partial(follower_import.commit_follower_list, rows=rows,
                    owner_entity_id=owner_entity_id, direction=direction,
                    observed_at=observed_at, agent="mcp:follower-import")
        )
    return await _run(
        partial(follower_import.preview_follower_list, rows=rows,
                owner_entity_id=owner_entity_id, direction=direction)
    )


# --- ASGI-Wiring ----------------------------------------------------------------


def build_mcp_asgi():
    """MCP-Sub-App inkl. Well-Known-Aliasse und korrigierter PRM-Scopes.

    Das SDK serviert die PRM nur pfad-suffigiert (…/oauth-protected-resource/mcp)
    und die AS-Metadata nur unsuffigiert — Clients proben alle vier Varianten,
    also beide fehlenden als Alias nachrüsten (Handler wiederverwendet, damit
    nichts driftet).

    Zudem annonciert das SDK als PRM-`scopes_supported` nur die `required_scopes`
    (welt:read); Clients (claude.ai) fragen dann nie welt:write an und bekommen
    einen read-only-Token. Darum die PRM-Route durch eine ersetzen, die beide
    `SCOPES` als supported meldet — required_scopes bleibt welt:read, damit
    welt:write optional ist und Lesen nicht bricht."""
    app = mcp.streamable_http_app()
    prm_path = "/.well-known/oauth-protected-resource/mcp"
    routes = [r for r in app.router.routes
              if not (isinstance(r, Route) and r.path == prm_path)]
    # PRM neu bauen (gleiche URLs wie die AuthSettings), aber mit beiden Scopes.
    prm_routes = create_protected_resource_routes(
        resource_url=AnyHttpUrl(f"{_PUBLIC_URL}/mcp"),
        authorization_servers=[AnyHttpUrl(_PUBLIC_URL)],
        scopes_supported=SCOPES,
    )
    routes.extend(prm_routes)
    routes.append(  # Alias ohne Pfad-Suffix (Clients proben beide Varianten)
        Route("/.well-known/oauth-protected-resource", prm_routes[0].endpoint,
              methods=["GET", "OPTIONS"])
    )
    by_path = {r.path: r for r in routes if isinstance(r, Route)}
    meta = by_path.get("/.well-known/oauth-authorization-server")
    if meta is not None:
        routes.append(
            Route("/.well-known/oauth-authorization-server/mcp", meta.endpoint,
                  methods=["GET", "OPTIONS"])
        )
    app.router.routes = routes
    return app


class McpPathDispatch:
    """ASGI-Weiche: MCP-Transport + OAuth-Protokoll-Pfade → MCP-Sub-App,
    alles andere (API, SPA, /oauth/login) → FastAPI.

    Ein Mount("/") ginge nicht: der SPA-Catch-all (GET /{path}) der FastAPI-App
    würde GET-Requests auf /authorize und die Well-Known-Pfade schlucken.
    Lifespan geht an die FastAPI-App — deren Lifespan treibt den
    MCP-Session-Manager (Starlette startet Sub-App-Lifespans nie selbst)."""

    _EXACT = frozenset({"/mcp", "/authorize", "/token", "/register", "/revoke"})
    _PREFIXES = (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
    )

    def __init__(self, fallback_app, mcp_asgi):
        self.fallback_app = fallback_app
        self.mcp_asgi = mcp_asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path in self._EXACT or path.startswith(self._PREFIXES):
                await self.mcp_asgi(scope, receive, send)
                return
        await self.fallback_app(scope, receive, send)
