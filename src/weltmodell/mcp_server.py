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
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import anyio
import psycopg
from psycopg.types.json import Jsonb
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

from . import (
    analysis,
    follower_import,
    pipeline,
    queries,
    registry,
    resolution,
    snapshot_import,
    statements,
)
from .config import get_public_url, is_prod
from .db import get_conn
from .entities import create_entities, create_entity, fix_entity
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


# --- Tool-Call-Log -------------------------------------------------------------

# Serialisierte Args oberhalb dieser Größe werden nicht gespeichert (Keys
# bleiben) — Bulk-Payloads würden das Log sonst aufblähen.
_ARGS_MAX_BYTES = 2048


def _log_tool_call(
    tool: str,
    arguments: dict[str, Any],
    duration_ms: int,
    status: str,
    error: str | None,
    result: Any,
    token_hash: str | None,
) -> None:
    """Eine Zeile nach mcp_tool_log schreiben (eigene Verbindung, läuft im
    Thread). Fehler fängt der Aufrufer — ein Log-Ausfall bricht nie den Call."""
    args_json = json.dumps(arguments, default=str)
    if len(args_json) > _ARGS_MAX_BYTES:
        args: dict[str, Any] = {"_truncated": True, **{k: "…" for k in arguments}}
    else:
        args = json.loads(args_json)  # garantiert JSON-clean (default=str oben)
    result_bytes = None
    if result is not None:
        try:
            result_bytes = len(json.dumps(to_jsonable_python(result), default=str))
        except Exception:
            pass  # Antwortgröße ist nice-to-have, nie ein Fehlergrund
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO mcp_tool_log"
            " (tool, args, duration_ms, status, error, result_bytes, token_hash)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tool, Jsonb(args), duration_ms, status, error, result_bytes, token_hash),
        )
        conn.commit()
    finally:
        conn.close()


class LoggedFastMCP(FastMCP):
    """Loggt jeden Tool-Call nach mcp_tool_log (Migration 0021): Tool, Args,
    Dauer, Status, Antwortgröße, Token-Hash — auswertbar über v_tool_log."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        token = get_access_token()
        token_hash = (
            hashlib.sha256(token.token.encode()).hexdigest()[:12] if token else None
        )
        start = time.monotonic()
        status, error, result = "ok", None, None
        try:
            result = await super().call_tool(name, arguments)
            return result
        except Exception as exc:
            status, error = "error", str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            try:
                await anyio.to_thread.run_sync(partial(
                    _log_tool_call, name, arguments, duration_ms,
                    status, error, result, token_hash,
                ))
            except Exception as exc:
                print(f"mcp_tool_log: INSERT fehlgeschlagen: {exc}", file=sys.stderr)


mcp = LoggedFastMCP(
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
    # Sicherheitsnetz: DB-Fehler, die die Python-Validierung nicht abfängt
    # (z. B. kaputte UUIDs/Datumsformate), als klare Meldung statt rohem
    # psycopg-Traceback ausliefern.
    except psycopg.DataError as exc:
        raise ToolError(f"Ungültige Eingabe: {_pg_message(exc)}")
    except psycopg.IntegrityError as exc:
        raise ToolError(f"Konflikt mit bestehenden Daten: {_pg_message(exc)}")
    return _compact(to_jsonable_python(result))


def _pg_message(exc: psycopg.Error) -> str:
    primary = exc.diag.message_primary if exc.diag else None
    return primary or str(exc).splitlines()[0]


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


# Kompakt-Sicht (Default): nur die Felder, die ein Agent zum korrekten
# Schreiben braucht. Volle Registry-Zeilen (Labels, Wikidata/schema.org-
# Mappings) nur mit full=true — halbiert die Antwortgröße.
_VOCAB_FIELDS = {
    "types": ("id", "parent_id", "kind", "abstract", "label_predicate"),
    "predicates": ("id", "domain_type", "domain_interface", "range_kind",
                   "range_type", "cardinality", "inverse_id", "identifying"),
    "interfaces": ("id",),
}


@mcp.tool()
async def welt_vocabulary(
    part: Literal["all", "types", "predicates", "interfaces"] = "all",
    full: bool = False,
) -> dict[str, Any]:
    """Registry-Vokabular: erlaubte Typen (mit Parent/kind), Prädikate (mit
    Domain, Range, Cardinality, identifying) und Interfaces. Nur dieses
    Vokabular darf in Statements verwendet werden — fehlt etwas, Proposal
    einreichen statt improvisieren. Default kompakt (Schreib-relevante
    Felder); full=true liefert alle Registry-Spalten (Labels, Wikidata-PIDs)."""

    def q(conn):
        vocab = registry.vocabulary(conn)
        if not full:
            vocab = {
                k: [
                    {f: row[f] for f in _VOCAB_FIELDS[k] if f in row}
                    for row in rows
                ]
                if k in _VOCAB_FIELDS else rows
                for k, rows in vocab.items()
            }
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
    Prädikaten (z. B. account_uri, email) matchen deterministisch
    (nicht-identifying Keys werden ignoriert und in warnings gemeldet);
    sonst liefert label Kandidaten: exakte Label-Gleichheit (similarity 1.0)
    + Vektor-Ähnlichkeit, type_id subtypfähig (Agent findet Person).
    match=null ⇒ kein sicherer Treffer; Teilnamen-Suche ist welt_search."""

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
    min_confidence: float | None = None,
    rank: Literal["preferred", "normal", "deprecated"] | None = None,
    statement_limit: int = 200,
    output: Literal["compact", "full"] = "compact",
) -> dict[str, Any]:
    """Vollsicht einer Entity: ausgehende und eingehende Statements.
    Zeitreisen: valid_at = „was war am Datum D wahr?", system_at = „was
    glaubte ich am Datum D?" (ISO-Datetime). min_confidence/rank filtern
    wie in welt_query (rank exakt). statement_limit kappt beide Listen;
    statements_total/incoming_total nennen die echte Zahl (Hub-Entities
    sprengen sonst den Kontext). output=compact (Default) lässt Qualifier
    + Quellen weg; output=full nur, wenn Provenance wirklich gebraucht wird."""
    if statement_limit < 1:
        raise ToolError("statement_limit muss >= 1 sein")

    def q(conn):
        view = queries.entity_view(
            conn, entity_id=entity_id, system_at=system_at, valid_at=valid_at,
            include_deprecated=include_deprecated,
            min_confidence=min_confidence, rank=rank, output=output,
        )
        view["statements_total"] = len(view["statements"])
        view["incoming_total"] = len(view["incoming"])
        view["statements"] = view["statements"][:statement_limit]
        view["incoming"] = view["incoming"][:statement_limit]
        return view

    return await _run(q)


@mcp.tool()
async def welt_query(
    subject_id: str | None = None,
    predicate_id: str | None = None,
    object_id: str | None = None,
    value_text: str | None = None,
    min_confidence: float | None = None,
    rank: Literal["preferred", "normal", "deprecated"] | None = None,
    valid_at: str | None = None,
    system_at: str | None = None,
    limit: int = 25,
    offset: int = 0,
    aggregate: Literal["count", "sum", "avg"] | None = None,
    group_by: Literal["subject", "object"] | None = None,
    output: Literal["ids", "compact", "full"] = "full",
) -> dict[str, Any]:
    """Statement-zentrierte Suche — viertes Standbein neben welt_search,
    welt_entity und welt_traverse. Alle Filter optional und kombinierbar:
    subject_id/object_id (folgen der Merge-Kette), predicate_id, value_text
    (exakt), min_confidence, rank (exakt; ohne rank ist deprecated
    ausgeblendet). Zeitreisen wie welt_entity: valid_at = „was war am Datum D
    wahr?", system_at = „was glaubte ich am Datum D?". Liefert Statements mit
    Qualifiern + Quellen (Serialisierung wie welt_entity) und total.
    aggregate=count|sum|avg statt der Liste; sum/avg nur über number- und
    quantity-Werte, bei quantity pro unit gruppiert; group_by=subject|object
    gruppiert nach Entity. output: full (Default) | compact (Statements ohne
    Qualifier/Quellen) | ids (nur Statement-IDs, limit bis 5000)."""
    return await _run(
        partial(queries.query_statements, subject_id=subject_id,
                predicate_id=predicate_id, object_id=object_id,
                value_text=value_text, min_confidence=min_confidence,
                rank=rank, valid_at=valid_at, system_at=system_at,
                limit=min(limit, 5000 if output == "ids" else 200),
                offset=offset, aggregate=aggregate, group_by=group_by,
                output=output)
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
    min_confidence: float | None = None,
    rank: Literal["preferred", "normal", "deprecated"] | None = None,
    output: Literal["ids", "compact", "full"] = "full",
) -> dict[str, Any]:
    """Graph-Nachbarschaft (k-Hop, ungerichtet, zyklensicher) als Teilgraph:
    nodes (id, label, type_id, degree, depth) + edges (subject→object mit
    Prädikat). predicates schränkt auf bestimmte Kanten ein. total_nodes
    nennt die echte Größe, wenn max_nodes kappt. min_confidence/rank filtern
    die Kanten wie in welt_query (rank exakt). output: full (Default,
    heutige Node/Edge-Form) | compact (nodes als {id, label, type_id}) |
    ids (nur Node-IDs, max_nodes bis 5000)."""
    return await _run(
        partial(queries.neighborhood, start_id=start_id, max_depth=max_depth,
                predicates=predicates,
                max_nodes=min(max_nodes, 5000 if output == "ids" else 500),
                min_confidence=min_confidence, rank=rank, output=output)
    )


# --- Analyse (read-only, ein Roundtrip statt vieler Einzel-Calls) ---------------


@mcp.tool()
async def welt_match(
    patterns: list[dict[str, Any]],
    select: list[str],
    min_confidence: float | None = None,
    valid_at: str | None = None,
    system_at: str | None = None,
    limit: int = 100,
    offset: int = 0,
    output: Literal["ids", "compact", "full"] = "compact",
) -> dict[str, Any]:
    """Konjunktives Triple-Pattern-Matching — DAS Werkzeug für Graph-Fragen
    wie „wer folgt sowohl A als auch B" oder „Accounts von Personen, die X
    kennt". patterns: Liste von {"s":…, "p":…, "o":…}; jede Position eine
    konkrete ID, eine Variable "?name" oder (nur am Objekt)
    {"value_text":"…"}. Gleiche Variable in mehreren Patterns = Join;
    Variablen sind auch an Prädikat-Position erlaubt (binden Prädikat-IDs).
    select nennt die zurückzugebenden Variablen. Merge-Ketten werden
    aufgelöst, deprecated ist unsichtbar; min_confidence/valid_at/system_at
    wie in welt_query. Liefert bindings + total (stabil über Pagination).
    Für Filter mit nur EINEM Pattern reicht welt_query."""
    return await _run(
        partial(analysis.match, patterns=patterns, select=select,
                min_confidence=min_confidence, valid_at=valid_at,
                system_at=system_at, limit=limit, offset=offset,
                output=output)
    )


@mcp.tool()
async def welt_set(
    operation: Literal["intersect", "union", "difference"],
    queries: list[dict[str, Any]],
    on: Literal["subject", "object"] = "subject",
    limit: int = 1000,
    output: Literal["ids", "compact", "full"] = "compact",
) -> dict[str, Any]:
    """Mengenalgebra über 2–10 Statement-Queries: intersect | union |
    difference (= erste Query minus alle weiteren). Jede Query ist ein
    Filterset wie in welt_query: {"predicate_id"?, "object_id"?,
    "subject_id"?, "value_text"?, "min_confidence"?, "valid_at"?}. on
    bestimmt, ob über subject- oder object-IDs operiert wird. Beispiel
    „folgt A, aber nicht B": difference über zwei follows-Queries mit
    on=subject. Liefert entities + total."""
    return await _run(
        partial(analysis.set_operation, operation=operation, queries=queries,
                on=on, limit=limit, output=output)
    )


@mcp.tool()
async def welt_path(
    start_id: str,
    end_id: str,
    max_depth: int = 4,
    max_paths: int = 5,
    predicates: list[str] | None = None,
    min_confidence: float | None = None,
    output: Literal["ids", "compact", "full"] = "compact",
) -> dict[str, Any]:
    """Kürzeste Pfade zwischen zwei Entities („über wen kennen sich X und
    Y?") — ungerichtet, zyklensicher, max_depth hart auf 6 gekappt. Liefert
    Pfade als geordnete Knoten- und Kantenlisten (Kante = subject, predicate,
    object, direction), kürzeste zuerst, plus path_length und total (Anzahl
    aller kürzesten Pfade). Kein Pfad → leere Liste mit message, kein Fehler.
    predicates/min_confidence filtern die Kanten. Für die ganze Nachbarschaft
    EINER Entity ist welt_traverse das richtige Tool."""
    return await _run(
        partial(analysis.paths, start_id=start_id, end_id=end_id,
                max_depth=max_depth, max_paths=max_paths,
                predicates=predicates, min_confidence=min_confidence,
                output=output)
    )


@mcp.tool()
async def welt_common(
    entity_ids: list[str],
    predicates: list[str] | None = None,
    direction: Literal["in", "out", "both"] = "both",
    min_shared: int = 2,
    limit: int = 200,
    output: Literal["ids", "compact", "full"] = "compact",
) -> dict[str, Any]:
    """Gemeinsame Nachbarn von 2–10 Entities (z. B. gemeinsame Follower
    zweier Accounts: direction=in, predicates=[follows]). direction aus
    Sicht der Eingabe-Entities: in = wer zeigt auf sie, out = worauf zeigen
    sie, both = beides. Pro Nachbar: entity, shared_with (welche
    Eingabe-Entities verbunden sind), shared_count; sortiert nach
    shared_count absteigend. min_shared filtert („mindestens 2 von 3")."""
    return await _run(
        partial(analysis.common_neighbors, entity_ids=entity_ids,
                predicates=predicates, direction=direction,
                min_shared=min_shared, limit=limit, output=output)
    )


@mcp.tool()
async def welt_rank(
    metric: Literal["degree", "pagerank", "betweenness"],
    predicates: list[str] | None = None,
    type_id: str | None = None,
    top: int = 20,
) -> dict[str, Any]:
    """Zentralität: die wichtigsten Knoten im Graphen. degree = Anzahl
    Kanten (Statements), pagerank = globale Wichtigkeit, betweenness =
    Brücken zwischen Regionen. predicates schränkt die Kanten ein, type_id
    filtert die Ergebnis-Knoten subtypfähig (Agent findet Person). Liefert
    Top-N als {id, label, type_id, score}."""
    return await _run(
        partial(analysis.rank_entities, metric=metric, predicates=predicates,
                type_id=type_id, top=top)
    )


@mcp.tool()
async def welt_cluster(
    predicates: list[str] | None = None,
    min_size: int = 3,
    algorithm: Literal["label_propagation", "louvain"] = "label_propagation",
) -> dict[str, Any]:
    """Community-Detection: zusammenhängende Gruppen (Freundeskreise,
    Themencluster) im (per predicates gefilterten) Graphen. Liefert Cluster
    mit Membern ({id, label, type_id}) und Größe, größte zuerst; min_size
    blendet Kleinstcluster aus. total nennt die Clusterzahl vor dem
    min_size-Filter."""
    return await _run(
        partial(analysis.cluster, predicates=predicates, min_size=min_size,
                algorithm=algorithm)
    )


@mcp.tool()
async def welt_similar(
    entity_id: str,
    predicates: list[str] | None = None,
    direction: Literal["in", "out", "both"] = "both",
    top: int = 10,
) -> dict[str, Any]:
    """Strukturell ähnliche Entities: Jaccard über Nachbarmengen (z. B.
    Accounts mit denselben Followern: direction=in). Liefert Top-N mit
    score (0–1) und overlap (Größe der Schnittmenge). Entity ohne Nachbarn
    → leere Liste. Für inhaltliche Ähnlichkeit über Embeddings ist
    welt_search das richtige Tool."""
    return await _run(
        partial(analysis.similar, entity_id=entity_id, predicates=predicates,
                direction=direction, top=top)
    )


@mcp.tool()
async def welt_changes(
    since: str,
    until: str | None = None,
    predicate_id: str | None = None,
    subject_id: str | None = None,
    kind: Literal["added", "deprecated", "all"] = "all",
    limit: int = 200,
    output: Literal["ids", "compact", "full"] = "compact",
) -> dict[str, Any]:
    """Was hat sich geändert? Diff auf der Systemzeit-Achse: Statements, die
    im Fenster [since, until] entstanden (added, inkl. neuer Versionen durch
    Supersession) oder deprecated wurden. since/until als ISO-Datetime,
    until Default jetzt. Jedes Statement trägt change und changed_at.
    Für die Historie EINER Entity ist welt_timeline das richtige Tool."""
    return await _run(
        partial(analysis.changes, since=since, until=until,
                predicate_id=predicate_id, subject_id=subject_id,
                kind=kind, limit=limit, output=output)
    )


@mcp.tool()
async def welt_sql(query: str, limit: int = 500) -> dict[str, Any]:
    """LETZTES MITTEL, wenn kein strukturiertes Tool passt (erst welt_query,
    welt_match, welt_set, welt_common prüfen!): genau EIN read-only SELECT
    gegen die dokumentierten Whitelist-Views —
    v_entities (id, type_id, label, merged_into, created_at),
    v_statements (id, subject_id, subject_label, predicate_id, object_id,
      object_label, value_type, value_text, value_number, value_unit,
      value_datetime, value_geojson, value_json, rank, confidence, origin,
      valid_from, valid_to, system_from, system_to; aktuelle Sicht =
      system_to IS NULL AND rank <> 'deprecated'),
    v_qualifiers (id, statement_id, predicate_id, value_type, value_text,
      value_number, value_datetime, object_id),
    v_sources (id, url, activity, agent, retrieved_at, statement_id — eine
      Zeile pro Quelle-Statement-Zuordnung),
    v_tool_log (id, ts, tool, args, duration_ms, status, error, result_bytes,
      token_hash — ein MCP-Tool-Call pro Zeile; eigene Nutzung analysieren).
    Erzwungen: SELECT-only (geparst), nur diese Views, 5 s Timeout,
    read-only Transaktion, Row-Cap limit. Merge-Ketten und
    deprecated-Filter musst du hier SELBST beachten (merged_into, rank)."""
    return await _run(partial(analysis.sql_query, query=query, limit=limit))


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

    def q(conn):
        doc = pipeline.ingest_document(
            conn, raw=raw or {}, url=url, activity=activity, agent=agent,
            retrieved_at=retrieved_at,
        )
        # Kein raw-Echo: der Aufrufer hat das Payload gerade selbst gesendet.
        return {k: v for k, v in doc.items() if k != "raw"}

    return await _run(q)


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
async def welt_fix_entity(entity_id: str, reason: str) -> dict[str, Any]:
    """ERRATUM: versehentlich angelegten Entity-Anker löschen — das
    Anker-Pendant zu welt_fix_statement, NUR für echte Fehler (Tippfehler-
    Anlage, falscher Typ, Testrest). Löscht nur, wenn die Entity null
    eingehende und null ausgehende nicht-deprecated Statements hat; sonst
    Fehler — Dubletten gehören zu welt_merge_entities (verlustfrei), nicht
    hierher. reason ist Pflicht und wird geloggt (Audit, wie bei
    welt_fix_statement)."""
    _require_write()
    return await _run(
        partial(fix_entity, entity_id=entity_id, reason=reason)
    )


@mcp.tool()
async def welt_merge_entities(entity_id: str, target_id: str) -> dict[str, Any]:
    """Dublette verlustfrei in den kanonischen Anker mergen: entity_id wird
    auf target_id umgebogen, Provenance beider Seiten bleibt erhalten.
    Nicht umkehrbar — vorher per welt_entity beide Seiten prüfen."""
    _require_write()
    return await _run(
        partial(resolution.merge_entity, source_id=entity_id, target_id=target_id)
    )


@mcp.tool()
async def welt_propose_type(
    type_id: str,
    kind: Literal["continuant", "occurrent"],
    label: str,
    parent_id: str | None = None,
    interfaces: list[str] | None = None,
    label_predicate: str | None = None,
    abstract: bool = False,
    wikidata_qid: str | None = None,
    rationale: str | None = None,
    proposed_by: str = "mcp-agent",
) -> dict[str, Any]:
    """Neuen Entity-Typ vorschlagen (Review-Gate, Invariante 2). Vorher den
    Entscheidungsbaum der Verfassung durchgehen — oft ist ein Statement oder
    ein vorhandener Typ die richtige Antwort. kind muss zum Parent passen
    (Continuant/Occurrent-Split); parent_id=null legt eine neue Wurzel an
    (Ausnahme — in vorhandene Äste hängen bleibt die Regel). label_predicate:
    welches Prädikat den Anzeige-Bezeichner trägt (muss existieren und
    domain-kompatibel sein). abstract=true macht den Typ nicht instanziierbar.
    rationale: warum dieser Ast, dieses kind."""
    _require_write()
    return await _run(
        partial(registry.propose_type, type_id=type_id, parent_id=parent_id,
                kind=kind, label=label, interfaces=interfaces or [],
                label_predicate=label_predicate, abstract=abstract,
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
    identifying: bool = False,
    wikidata_pid: str | None = None,
    schema_org: str | None = None,
    rationale: str | None = None,
    proposed_by: str = "mcp-agent",
) -> dict[str, Any]:
    """Neues Prädikat vorschlagen (Review-Gate). Domain so hoch wie möglich
    (Typ ODER Interface), Range eng aber subtyp-offen, scharfe Rollen-Prädikate
    statt participant+Qualifier, Verfeinerung ist Qualifier-Job.
    identifying=true macht das Prädikat zum harten Dedup-Key (§7.2) —
    erfordert range_kind='string' + cardinality='1:1'; die DB erzwingt dann
    Eindeutigkeit pro Wert. wikidata_pid/schema_org mitgeben, wo es das
    extern gibt."""
    _require_write()
    return await _run(
        partial(registry.propose_predicate, predicate_id=predicate_id,
                label=label, range_kind=range_kind, domain_type=domain_type,
                domain_interface=domain_interface, range_type=range_type,
                cardinality=cardinality, inverse_id=inverse_id,
                identifying=identifying, wikidata_pid=wikidata_pid,
                schema_org=schema_org, rationale=rationale,
                proposed_by=proposed_by)
    )


@mcp.tool()
async def welt_decide_proposal(
    kind: Literal["type", "predicate", "interface"],
    proposal_id: str,
    decision: Literal["approve", "reject"],
) -> dict[str, Any]:
    """Proposal entscheiden. Approve prüft hart (Parent-kind-Match, Domain/
    Range existieren) und schreibt in die Registry. Inverse Prädikat-Paare
    (a.inverse=b, b.inverse=a): beide proposen, EINES approven — das pending
    Gegenstück wird atomar mit angelegt. Vor dem Approve gegen die
    Design-Regeln der Verfassung prüfen — ein falsch approbierter Typ ist
    teuer. Bei Zweifel: reject — oder welt_amend_proposal zum Nachschärfen."""
    _require_write()

    def q(conn):
        if decision == "approve":
            if kind == "type":
                return registry.approve_type(conn, proposal_id)
            if kind == "predicate":
                return registry.approve_predicate(conn, proposal_id)
            return registry.approve_interface(conn, proposal_id)
        return registry.reject_proposal(conn, f"proposed_{kind}", proposal_id)

    return await _run(q)


@mcp.tool()
async def welt_propose_interface(
    interface_id: str,
    label: str,
    rationale: str | None = None,
    proposed_by: str = "mcp-agent",
) -> dict[str, Any]:
    """Neues Interface vorschlagen (Review-Gate). Interfaces bündeln
    Fähigkeiten über Typ-Äste hinweg (Nameable, Locatable, …) — nur bei
    echter Wiederkehr über mehrere Äste, sonst reicht die Typ-Hierarchie.
    Nach dem Approve in welt_propose_type (interfaces) und
    welt_propose_predicate (domain_interface) referenzierbar."""
    _require_write()
    return await _run(
        partial(registry.propose_interface, interface_id=interface_id,
                label=label, rationale=rationale, proposed_by=proposed_by)
    )


@mcp.tool()
async def welt_amend_proposal(
    proposal_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Proposal nachschärfen statt neu einreichen: patch ist ein Teilobjekt
    der ursprünglichen Propose-Felder (z. B. {"cardinality": "1:1"}). Nur auf
    pending oder rejected — approved ist unveränderlich. Ein Amend auf
    rejected setzt den Status zurück auf pending."""
    _require_write()
    return await _run(
        partial(registry.amend_proposal, proposal_id=proposal_id, patch=patch)
    )


@mcp.tool()
async def welt_propose_types(
    proposals: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """BULK-Variante von welt_propose_type (ein Roundtrip statt N). Jedes
    Element trägt die Felder von welt_propose_type: {"type_id":…, "kind":…,
    "label":…, "parent_id"?:…, "interfaces"?:…, "label_predicate"?:…,
    "abstract"?:…, "wikidata_qid"?:…, "rationale"?:…}.
    atomic=true (Default): alles-oder-nichts, der erste Fehler bricht ab und
    nennt den Item-Index. atomic=false: Best-Effort mit results-Report
    (index, ok, error/id) wie welt_create_entities."""
    _require_write()
    return await _run(partial(registry.propose_types, items=proposals, atomic=atomic))


@mcp.tool()
async def welt_propose_predicates(
    proposals: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """BULK-Variante von welt_propose_predicate (ein Roundtrip statt N). Jedes
    Element trägt die Felder von welt_propose_predicate: {"predicate_id":…,
    "label":…, "range_kind":…, "domain_type"?:…, "domain_interface"?:…,
    "range_type"?:…, "cardinality"?:…, "inverse_id"?:…, "identifying"?:…,
    "wikidata_pid"?:…, "schema_org"?:…, "rationale"?:…}.
    atomic wie bei welt_propose_types."""
    _require_write()
    return await _run(
        partial(registry.propose_predicates, items=proposals, atomic=atomic)
    )


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
async def welt_import_snapshot(
    predicate_id: str,
    owner_entity_id: str,
    rows: list[dict[str, Any]],
    mode: Literal["preview", "commit"] = "preview",
    direction: Literal["outgoing", "incoming"] = "outgoing",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Generischer Snapshot-Import für beliebige n:m-Entity-Prädikate
    (Verallgemeinerung von welt_import_follower_list). rows:
    [{"type_id"?:…, "label"?:…, "identifiers"?:{prädikat: wert},
      "statements"?:[{"predicate_id":…, "value":{…}}]}]
    type_id-Default ist der Range- bzw. Domain-Typ des Prädikats; identifiers
    dedupen deterministisch; statements werden nur bei NEUANLAGE der
    Ziel-Entity mitcommittet. direction: outgoing = Owner ist Subjekt
    (owner → target), incoming = Owner ist Objekt (target → owner).
    IMMER erst mode='preview' (read-only, klassifiziert jede Row:
    new_entity/new_statement/confirmed/invalid), Ergebnis prüfen, dann
    mode='commit'. Commit re-bestätigt Bekanntes per Reference statt zu
    duplizieren (Snapshot-Philosophie: Abwesenheit ist kein Gegenbeweis)."""
    if mode == "commit":
        _require_write()
        return await _run(
            partial(snapshot_import.commit_snapshot, predicate_id=predicate_id,
                    owner_entity_id=owner_entity_id, rows=rows,
                    direction=direction, observed_at=observed_at,
                    agent="mcp:snapshot-import")
        )
    return await _run(
        partial(snapshot_import.preview_snapshot, predicate_id=predicate_id,
                owner_entity_id=owner_entity_id, rows=rows, direction=direction)
    )


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
