"""Analyse-Tools: komplexe Graph-Fragen in einem Roundtrip (read-only).

Alles hier ist ableitbare Lese-Sicht über die aktuellen Statements
(Invariante 1). Der Graph ist klein (Planungshorizont 100k Statements) —
In-Memory-Algorithmen über einen pro Request geladenen Kantensatz sind
bewusst ok; kein Caching, keine Materialisierung.

Gemeinsame Semantik (wie queries.query_statements):
- Eingabe-IDs folgen der Merge-Kette (canonical_id).
- Ohne expliziten rank-Filter sind deprecated Statements unsichtbar.
- output = ids | compact | full steuert die Serialisierung; ids erlaubt
  Limits bis 5000, compact/full bis 500.
"""

import re
from datetime import datetime
from typing import Any

import igraph as ig
import psycopg
import sqlglot
from sqlglot import expressions as exp

from .entities import canonical_id
from .errors import ValidationError
from .queries import _TIME_FILTER, _attach_qualifiers_and_references, check_min_confidence
from .registry import check_predicates, descendant_type_ids, unknown_type_message

OUTPUTS = ("ids", "compact", "full")
IDS_MAX = 5000
LIST_MAX = 500

# Aktuelle Entity-Kanten, merge-bereinigt — dieselben Filter wie
# graph_metrics._EDGES_SQL, plus optionale Prädikat-/Confidence-Filter.
_EDGES_SQL = """
    SELECT s.id, s.subject_id, s.object_id, s.predicate_id, s.confidence
    FROM statement s
    JOIN entity a ON a.id = s.subject_id AND a.merged_into IS NULL
    JOIN entity b ON b.id = s.object_id AND b.merged_into IS NULL
    WHERE s.value_type = 'entity' AND s.system_to IS NULL
      AND s.rank <> 'deprecated'
      AND (%(preds)s::text[] IS NULL OR s.predicate_id = ANY(%(preds)s))
      AND (%(min_confidence)s::real IS NULL
           OR s.confidence >= %(min_confidence)s)
"""

_STATEMENT_SELECT = """
    SELECT s.*, e.label AS object_label, e.type_id AS object_type,
           subj.label AS subject_label, subj.type_id AS subject_type,
           ST_AsGeoJSON(s.value_geo)::jsonb AS value_geojson
    FROM statement s
    LEFT JOIN entity e ON e.id = s.object_id
    LEFT JOIN entity subj ON subj.id = s.subject_id
"""


# --- Gemeinsame Helpers ---------------------------------------------------------


def _check_output(output: str) -> None:
    if output not in OUTPUTS:
        raise ValidationError(
            f"Ungültiges output '{output}' (erlaubt: {', '.join(OUTPUTS)})"
        )


def _effective_limit(limit: int, output: str) -> int:
    if limit < 1:
        raise ValidationError("limit muss >= 1 sein")
    return min(limit, IDS_MAX if output == "ids" else LIST_MAX)


def _check_predicates(conn: psycopg.Connection, predicates: list[str] | None) -> None:
    check_predicates(conn, predicates)  # zentral, mit „meintest du …?"-Vorschlag


def _load_edges(
    conn: psycopg.Connection,
    *,
    predicates: list[str] | None = None,
    min_confidence: float | None = None,
) -> list[dict[str, Any]]:
    _check_predicates(conn, predicates)
    check_min_confidence(min_confidence)
    return conn.execute(
        _EDGES_SQL, {"preds": predicates, "min_confidence": min_confidence}
    ).fetchall()


def serialize_entities(
    conn: psycopg.Connection, ids: list[str], output: str
) -> list[Any]:
    """Einheitliche Entity-Serialisierung aller Analyse-Tools.

    ids: nur ID-Strings. compact: {id, label, type_id}. full: der volle
    Entity-Anker wie in welt_entity (id, type_id, label, merged_into,
    created_at). Reihenfolge der Eingabe bleibt erhalten.
    """
    ids = [str(i) for i in ids]
    if output == "ids":
        return ids
    cols = "id, label, type_id" if output == "compact" \
        else "id, type_id, label, merged_into, created_at"
    rows = conn.execute(
        f"SELECT {cols} FROM entity WHERE id = ANY(%s::uuid[])", (ids,)
    ).fetchall()
    by_id = {str(r["id"]): {**r, "id": str(r["id"])} for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def serialize_statements(
    conn: psycopg.Connection, rows: list[dict[str, Any]], output: str
) -> list[Any]:
    """Einheitliche Statement-Serialisierung: ids = nur Statement-IDs,
    compact = Zeile ohne Qualifier/Quellen, full = exakt wie welt_entity
    mit output=full (mit Qualifiern + Quellen)."""
    if output == "ids":
        return [str(r["id"]) for r in rows]
    for r in rows:
        r.pop("value_geo", None)
    if output == "full":
        _attach_qualifiers_and_references(conn, rows)
    return rows


def _canonical(conn: psycopg.Connection, entity_id: str) -> str:
    """canonical_id mit Schutz vor kaputten UUIDs (klare Meldung statt 22P02)."""
    try:
        return canonical_id(conn, entity_id)
    except psycopg.DataError:
        raise ValidationError(f"'{entity_id}' ist keine gültige Entity-ID (UUID)")


def _neighbor_sets(
    edges: list[dict[str, Any]], direction: str
) -> dict[str, set[str]]:
    """Nachbarmengen pro Knoten. direction: out = Subjekt→Objekt,
    in = Objekt→Subjekt, both = ungerichtet."""
    nb: dict[str, set[str]] = {}
    for e in edges:
        s, o = str(e["subject_id"]), str(e["object_id"])
        if direction in ("out", "both"):
            nb.setdefault(s, set()).add(o)
        if direction in ("in", "both"):
            nb.setdefault(o, set()).add(s)
    return nb


def _check_direction(direction: str) -> None:
    if direction not in ("in", "out", "both"):
        raise ValidationError(
            f"Ungültige direction '{direction}' (erlaubt: in, out, both)"
        )


def _build_igraph(
    edges: list[dict[str, Any]],
) -> tuple[ig.Graph, list[str]]:
    """Ungerichteter Multigraph über die Kanten-Endpunkte."""
    nodes: list[str] = []
    index: dict[str, int] = {}
    pairs: list[tuple[int, int]] = []
    for e in edges:
        for eid in (str(e["subject_id"]), str(e["object_id"])):
            if eid not in index:
                index[eid] = len(nodes)
                nodes.append(eid)
        pairs.append((index[str(e["subject_id"])], index[str(e["object_id"])]))
    return ig.Graph(n=len(nodes), edges=pairs, directed=False), nodes


# --- welt_match: konjunktives Triple-Pattern-Matching ---------------------------

_VAR = re.compile(r"^\?[A-Za-z_][A-Za-z0-9_]*$")


def _parse_position(
    conn: psycopg.Connection, value: Any, *, position: str
) -> tuple[str, Any]:
    """Eine Pattern-Position klassifizieren: ('var', name) | ('const', wert)
    | ('text', wert) — Konstanten werden validiert/kanonisiert."""
    if isinstance(value, str) and value.startswith("?"):
        if not _VAR.match(value):
            raise ValidationError(
                f"Ungültiger Variablenname '{value}' (Form: ?name)"
            )
        return "var", value
    if position == "o" and isinstance(value, dict):
        if set(value.keys()) != {"value_text"}:
            raise ValidationError(
                "Objekt-Literal muss die Form {\"value_text\": \"…\"} haben"
            )
        return "text", value["value_text"]
    if not isinstance(value, str) or not value:
        raise ValidationError(
            f"Pattern-Position '{position}' muss eine ID, eine ?variable "
            "oder (nur am Objekt) {\"value_text\": …} sein"
        )
    if position == "p":
        _check_predicates(conn, [value])
        return "const", value
    return "const", _canonical(conn, value)


def match(
    conn: psycopg.Connection,
    *,
    patterns: list[dict[str, Any]],
    select: list[str],
    min_confidence: float | None = None,
    valid_at: Any = None,
    system_at: Any = None,
    limit: int = 100,
    offset: int = 0,
    output: str = "compact",
) -> dict[str, Any]:
    """Konjunktives Triple-Pattern-Matching: gleiche Variable in mehreren
    Patterns = Join. Variablen an Subjekt-/Objekt-Position binden Entities,
    an Prädikat-Position Prädikat-IDs (immer als String serialisiert)."""
    _check_output(output)
    if not patterns:
        raise ValidationError(
            "patterns darf nicht leer sein — für einfache Filter reicht welt_query."
        )
    if not select:
        raise ValidationError("select muss mindestens eine Variable nennen")
    if offset < 0:
        raise ValidationError("offset muss >= 0 sein")
    check_min_confidence(min_confidence)
    eff_limit = _effective_limit(limit, output)

    parsed: list[dict[str, tuple[str, Any]]] = []
    var_kinds: dict[str, str] = {}  # ?name → 'entity' | 'predicate'
    for i, pat in enumerate(patterns):
        if not isinstance(pat, dict) or set(pat.keys()) - {"s", "p", "o"} \
                or not all(k in pat for k in ("s", "p", "o")):
            raise ValidationError(
                f"Pattern {i} muss genau die Schlüssel s, p, o haben"
            )
        pos = {k: _parse_position(conn, pat[k], position=k) for k in ("s", "p", "o")}
        for k in ("s", "p", "o"):
            kind, name = pos[k]
            if kind != "var":
                continue
            var_kind = "predicate" if k == "p" else "entity"
            if var_kinds.setdefault(name, var_kind) != var_kind:
                raise ValidationError(
                    f"Variable {name} wird an Entity- UND Prädikat-Position "
                    "verwendet — das kann nie matchen."
                )
        parsed.append(pos)

    unknown = [v for v in select if v not in var_kinds]
    if unknown:
        raise ValidationError(
            f"select nennt Variablen, die in keinem Pattern vorkommen: "
            f"{', '.join(unknown)}"
        )

    # Pattern-weise fetchen (Konstanten als SQL-Filter), dann Hash-Join in
    # Python — beim Bestandsvolumen billiger als ein generierter Mega-JOIN.
    bindings: list[dict[str, str]] = [{}]
    for pos in parsed:
        params: dict[str, Any] = {
            "subject_id": pos["s"][1] if pos["s"][0] == "const" else None,
            "predicate_id": pos["p"][1] if pos["p"][0] == "const" else None,
            "object_id": pos["o"][1] if pos["o"][0] == "const" else None,
            "value_text": pos["o"][1] if pos["o"][0] == "text" else None,
            "need_entity_object": pos["o"][0] == "var",
            "min_confidence": min_confidence,
            "valid_at": valid_at,
            "system_at": system_at,
        }
        rows = conn.execute(
            f"""SELECT s.subject_id, s.predicate_id, s.object_id
                FROM statement s
                WHERE s.rank <> 'deprecated'
                  AND (%(subject_id)s::uuid IS NULL OR s.subject_id = %(subject_id)s)
                  AND (%(predicate_id)s::text IS NULL
                       OR s.predicate_id = %(predicate_id)s)
                  AND (%(object_id)s::uuid IS NULL OR s.object_id = %(object_id)s)
                  AND (%(value_text)s::text IS NULL OR s.value_text = %(value_text)s)
                  AND (NOT %(need_entity_object)s OR s.value_type = 'entity')
                  AND (%(min_confidence)s::real IS NULL
                       OR s.confidence >= %(min_confidence)s)
                  {_TIME_FILTER}""",
            params,
        ).fetchall()
        row_values = [
            {"s": str(r["subject_id"]), "p": r["predicate_id"],
             "o": str(r["object_id"]) if r["object_id"] else None}
            for r in rows
        ]
        next_bindings: list[dict[str, str]] = []
        for b in bindings:
            for rv in row_values:
                nb = dict(b)
                ok = True
                for k in ("s", "p", "o"):
                    kind, name = pos[k]
                    if kind != "var":
                        continue
                    bound = nb.get(name)
                    if bound is None:
                        nb[name] = rv[k]
                    elif bound != rv[k]:
                        ok = False
                        break
                if ok:
                    next_bindings.append(nb)
        bindings = next_bindings
        if not bindings:
            break

    # Auf select projizieren, deduplizieren, deterministisch sortieren —
    # sonst ist total/Pagination über Seiten nicht stabil.
    projected = sorted({tuple(b[v] for v in select) for b in bindings})
    total = len(projected)
    page = projected[offset:offset + eff_limit]

    entity_ids = sorted({
        val for row in page
        for var, val in zip(select, row) if var_kinds[var] == "entity"
    })
    serialized = dict(zip(entity_ids, serialize_entities(conn, entity_ids, output)))
    result_bindings = [
        {
            var: (val if var_kinds[var] == "predicate" else serialized.get(val, val))
            for var, val in zip(select, row)
        }
        for row in page
    ]
    return {"bindings": result_bindings, "total": total,
            "limit": eff_limit, "offset": offset}


# --- welt_set: Mengenalgebra über Query-Ergebnisse -------------------------------

_SET_QUERY_KEYS = {"predicate_id", "object_id", "subject_id", "value_text",
                   "min_confidence", "valid_at"}


def set_operation(
    conn: psycopg.Connection,
    *,
    operation: str,
    queries: list[dict[str, Any]],
    on: str = "subject",
    limit: int = 1000,
    output: str = "compact",
) -> dict[str, Any]:
    """Mengenalgebra (intersect/union/difference) über die subject- bzw.
    object-IDs mehrerer welt_query-artiger Filtersets."""
    _check_output(output)
    if operation not in ("intersect", "union", "difference"):
        raise ValidationError(
            f"Ungültige operation '{operation}' "
            "(erlaubt: intersect, union, difference)"
        )
    if on not in ("subject", "object"):
        raise ValidationError(f"Ungültiges on '{on}' (erlaubt: subject, object)")
    if not 2 <= len(queries) <= 10:
        raise ValidationError("queries braucht 2 bis 10 Filtersets")
    eff_limit = _effective_limit(limit, output)

    sets: list[set[str]] = []
    col = "subject_id" if on == "subject" else "object_id"
    for i, q in enumerate(queries):
        if not isinstance(q, dict):
            raise ValidationError(f"Query {i} muss ein Objekt sein")
        extra = set(q.keys()) - _SET_QUERY_KEYS
        if extra:
            raise ValidationError(
                f"Query {i}: unbekannte Filter {', '.join(sorted(extra))} "
                f"(erlaubt: {', '.join(sorted(_SET_QUERY_KEYS))})"
            )
        params = {
            "subject_id": _canonical(conn, q["subject_id"])
            if q.get("subject_id") else None,
            "predicate_id": q.get("predicate_id"),
            "object_id": _canonical(conn, q["object_id"])
            if q.get("object_id") else None,
            "value_text": q.get("value_text"),
            "min_confidence": q.get("min_confidence"),
            "valid_at": q.get("valid_at"),
            "system_at": None,
        }
        check_min_confidence(params["min_confidence"])
        if params["predicate_id"]:
            _check_predicates(conn, [params["predicate_id"]])
        rows = conn.execute(
            f"""SELECT DISTINCT s.{col} AS id
                FROM statement s
                WHERE s.rank <> 'deprecated'
                  AND s.{col} IS NOT NULL
                  AND (%(subject_id)s::uuid IS NULL OR s.subject_id = %(subject_id)s)
                  AND (%(predicate_id)s::text IS NULL
                       OR s.predicate_id = %(predicate_id)s)
                  AND (%(object_id)s::uuid IS NULL OR s.object_id = %(object_id)s)
                  AND (%(value_text)s::text IS NULL OR s.value_text = %(value_text)s)
                  AND (%(min_confidence)s::real IS NULL
                       OR s.confidence >= %(min_confidence)s)
                  {_TIME_FILTER}""",
            params,
        ).fetchall()
        sets.append({str(r["id"]) for r in rows})

    if operation == "intersect":
        result = set.intersection(*sets)
    elif operation == "union":
        result = set.union(*sets)
    else:
        result = sets[0].difference(*sets[1:])

    ordered = sorted(result)
    total = len(ordered)
    return {
        "entities": serialize_entities(conn, ordered[:eff_limit], output),
        "total": total, "operation": operation, "on": on, "limit": eff_limit,
    }


# --- welt_path: kürzeste Pfade ---------------------------------------------------


def paths(
    conn: psycopg.Connection,
    *,
    start_id: str,
    end_id: str,
    max_depth: int = 4,
    max_paths: int = 5,
    predicates: list[str] | None = None,
    min_confidence: float | None = None,
    output: str = "compact",
) -> dict[str, Any]:
    """Kürzeste Pfade (ungerichtet, zyklensicher) via BFS + Parent-DAG.
    total zählt ALLE kürzesten Pfade (DP), geliefert werden max_paths."""
    _check_output(output)
    if max_depth < 1:
        raise ValidationError("max_depth muss >= 1 sein")
    if max_paths < 1:
        raise ValidationError("max_paths muss >= 1 sein")
    max_paths = min(max_paths, 500)  # Deckel wie max_depth: kein Pfad-Dump
    max_depth = min(max_depth, 6)
    start = _canonical(conn, start_id)
    end = _canonical(conn, end_id)

    edges = _load_edges(conn, predicates=predicates, min_confidence=min_confidence)
    # Adjazenz mit einer repräsentativen Kante pro Knotenpaar (parallele
    # Statements würden sonst Pfad-Duplikate über dieselben Knoten erzeugen);
    # deterministisch: höchste Confidence, dann Statement-ID.
    adj: dict[str, dict[str, dict[str, Any]]] = {}
    for e in sorted(edges, key=lambda e: (-e["confidence"], str(e["id"]))):
        s, o = str(e["subject_id"]), str(e["object_id"])
        adj.setdefault(s, {}).setdefault(o, e)
        adj.setdefault(o, {}).setdefault(s, e)

    if start == end:
        return {"paths": [{
            "nodes": serialize_entities(conn, [start], output),
            "edges": [], "path_length": 0,
        }], "total": 1, "path_length": 0, "max_paths": max_paths}

    # BFS-Ebenen ab start: parents = Kürzeste-Pfade-DAG, counts = Anzahl
    # kürzester Pfade pro Knoten (für total ohne Enumeration).
    dist: dict[str, int] = {start: 0}
    parents: dict[str, list[str]] = {}
    counts: dict[str, int] = {start: 1}
    frontier = [start]
    depth = 0
    while frontier and depth < max_depth and end not in dist:
        depth += 1
        nxt: list[str] = []
        for node in frontier:
            for neighbor in adj.get(node, {}):
                if neighbor in dist and dist[neighbor] < depth:
                    continue
                if neighbor not in dist:
                    dist[neighbor] = depth
                    nxt.append(neighbor)
                parents.setdefault(neighbor, []).append(node)
                counts[neighbor] = counts.get(neighbor, 0) + counts[node]
        frontier = nxt

    if end not in dist:
        return {
            "paths": [], "total": 0, "max_paths": max_paths,
            "message": f"Kein Pfad zwischen {start} und {end} "
                       f"innerhalb von max_depth={max_depth} gefunden.",
        }

    # Rückwärts-Enumeration über den DAG, deterministisch, bis max_paths.
    node_paths: list[list[str]] = []

    def walk(node: str, tail: list[str]) -> None:
        if len(node_paths) >= max_paths:
            return
        if node == start:
            node_paths.append([start, *reversed(tail)])
            return
        for p in sorted(parents[node]):
            walk(p, [*tail, node])

    walk(end, [])
    total = counts[end]
    path_length = dist[end]

    all_ids = sorted({n for p in node_paths for n in p})
    ser = dict(zip(all_ids, serialize_entities(conn, all_ids, output)))
    out_paths = []
    for p in node_paths:
        path_edges = []
        for a, b in zip(p, p[1:]):
            e = adj[a][b]
            path_edges.append({
                "subject": str(e["subject_id"]),
                "predicate": e["predicate_id"],
                "object": str(e["object_id"]),
                "direction": "out" if str(e["subject_id"]) == a else "in",
            })
        out_paths.append({
            "nodes": [ser[n] for n in p],
            "edges": path_edges,
            "path_length": path_length,
        })
    return {"paths": out_paths, "total": total,
            "path_length": path_length, "max_paths": max_paths}


# --- welt_common: gemeinsame Nachbarn -------------------------------------------


def common_neighbors(
    conn: psycopg.Connection,
    *,
    entity_ids: list[str],
    predicates: list[str] | None = None,
    direction: str = "both",
    min_shared: int = 2,
    limit: int = 200,
    output: str = "compact",
) -> dict[str, Any]:
    """Gemeinsame Nachbarn von 2–10 Entities, sortiert nach shared_count.
    Die Eingabe-Entities selbst zählen nicht als Nachbarn."""
    _check_output(output)
    _check_direction(direction)
    if not 2 <= len(entity_ids) <= 10:
        raise ValidationError("entity_ids braucht 2 bis 10 Entities")
    if min_shared < 1:
        raise ValidationError("min_shared muss >= 1 sein")
    eff_limit = _effective_limit(limit, output)

    inputs = [_canonical(conn, e) for e in entity_ids]
    if len(set(inputs)) < len(inputs):
        raise ValidationError(
            "entity_ids enthält Duplikate (ggf. erst nach Merge-Auflösung) — "
            "jede Entity nur einmal angeben."
        )

    edges = _load_edges(conn, predicates=predicates)
    # direction aus Sicht der EINGABE-Entities: in = wer zeigt auf sie
    # (Follower), out = worauf zeigen sie (Gefolgte).
    nb = _neighbor_sets(edges, direction)
    input_set = set(inputs)
    shared: dict[str, list[str]] = {}
    for i in inputs:
        for neighbor in nb.get(i, set()):
            if neighbor in input_set:
                continue
            shared.setdefault(neighbor, []).append(i)

    items = sorted(
        ((n, srcs) for n, srcs in shared.items() if len(srcs) >= min_shared),
        key=lambda t: (-len(t[1]), t[0]),
    )
    total = len(items)
    page = items[:eff_limit]
    ser = dict(zip(
        [n for n, _ in page],
        serialize_entities(conn, [n for n, _ in page], output),
    ))
    return {
        "neighbors": [
            {"entity": ser[n], "shared_with": srcs, "shared_count": len(srcs)}
            for n, srcs in page
        ],
        "total": total, "min_shared": min_shared, "limit": eff_limit,
    }


# --- welt_rank: Zentralität ------------------------------------------------------


def rank_entities(
    conn: psycopg.Connection,
    *,
    metric: str,
    predicates: list[str] | None = None,
    type_id: str | None = None,
    top: int = 20,
) -> dict[str, Any]:
    """Zentralität (degree/pagerank/betweenness) über den gefilterten
    Kantensatz; type_id filtert die ERGEBNIS-Knoten subtypfähig, die Metrik
    rechnet auf dem vollen (Prädikat-)Graphen."""
    if metric not in ("degree", "pagerank", "betweenness"):
        raise ValidationError(
            f"Ungültige metric '{metric}' "
            "(erlaubt: degree, pagerank, betweenness)"
        )
    if top < 1:
        raise ValidationError("top muss >= 1 sein")
    top = min(top, 200)
    allowed_types: list[str] | None = None
    if type_id is not None:
        if not conn.execute(
            "SELECT 1 FROM entity_type WHERE id = %s", (type_id,)
        ).fetchone():
            raise ValidationError(unknown_type_message(conn, type_id))
        allowed_types = descendant_type_ids(conn, type_id)

    edges = _load_edges(conn, predicates=predicates)
    g, nodes = _build_igraph(edges)
    if not nodes:
        return {"items": [], "total": 0, "metric": metric}
    if metric == "degree":
        scores = g.degree()
    elif metric == "pagerank":
        scores = g.pagerank()
    else:
        scores = g.betweenness()

    ents = {
        str(r["id"]): r
        for r in conn.execute(
            "SELECT id, label, type_id FROM entity WHERE id = ANY(%s::uuid[])",
            (nodes,),
        ).fetchall()
    }
    scored = [
        {"id": n, "label": ents[n]["label"], "type_id": ents[n]["type_id"],
         "score": float(s)}
        for n, s in zip(nodes, scores)
        if allowed_types is None or ents[n]["type_id"] in allowed_types
    ]
    scored.sort(key=lambda r: (-r["score"], r["id"]))
    return {"items": scored[:top], "total": len(scored), "metric": metric,
            "top": top}


# --- welt_cluster: Community-Detection ------------------------------------------


def cluster(
    conn: psycopg.Connection,
    *,
    predicates: list[str] | None = None,
    min_size: int = 3,
    algorithm: str = "label_propagation",
    member_limit: int = 25,
) -> dict[str, Any]:
    """Community-Detection (igraph) über den gefilterten Kantensatz.
    Rückgabe: Cluster mit compact-Membern (max. member_limit pro Cluster,
    size nennt die echte Größe), größte zuerst."""
    if algorithm not in ("label_propagation", "louvain"):
        raise ValidationError(
            f"Ungültiger algorithm '{algorithm}' "
            "(erlaubt: label_propagation, louvain)"
        )
    if min_size < 1:
        raise ValidationError("min_size muss >= 1 sein")
    if member_limit < 1:
        raise ValidationError("member_limit muss >= 1 sein")

    edges = _load_edges(conn, predicates=predicates)
    g, nodes = _build_igraph(edges)
    if not nodes:
        return {"clusters": [], "total": 0, "algorithm": algorithm}
    g.simplify(multiple=True, loops=True)
    if algorithm == "label_propagation":
        membership = g.community_label_propagation().membership
    else:
        membership = g.community_multilevel().membership

    groups: dict[int, list[str]] = {}
    for node, comm in zip(nodes, membership):
        groups.setdefault(comm, []).append(node)
    total = len(groups)
    kept = sorted(
        (sorted(members) for members in groups.values() if len(members) >= min_size),
        key=lambda m: (-len(m), m[0]),
    )
    all_ids = [n for m in kept for n in m[:member_limit]]
    ser = dict(zip(all_ids, serialize_entities(conn, all_ids, "compact")))
    return {
        "clusters": [
            {"size": len(m), "members": [ser[n] for n in m[:member_limit]]}
            for m in kept
        ],
        "total": total, "min_size": min_size, "algorithm": algorithm,
    }


# --- welt_similar: strukturelle Ähnlichkeit --------------------------------------


def similar(
    conn: psycopg.Connection,
    *,
    entity_id: str,
    predicates: list[str] | None = None,
    direction: str = "both",
    top: int = 10,
) -> dict[str, Any]:
    """Jaccard-Ähnlichkeit über Nachbarmengen: Kandidaten sind alle Knoten,
    die mindestens einen Nachbarn mit der Entity teilen."""
    _check_direction(direction)
    if top < 1:
        raise ValidationError("top muss >= 1 sein")
    top = min(top, 200)
    entity = _canonical(conn, entity_id)

    edges = _load_edges(conn, predicates=predicates)
    nb = _neighbor_sets(edges, direction)
    own = nb.get(entity, set())
    if not own:
        return {"items": [], "total": 0,
                "message": "Entity hat im gefilterten Graphen keine Nachbarn."}

    # Kandidaten über die Gegenrichtung der Nachbar-Kanten finden: wer teilt
    # mindestens einen Nachbarn? (reverse von direction, damit Kandidaten
    # dieselbe Nachbar-Semantik vergleichen)
    reverse = {"in": "out", "out": "in", "both": "both"}[direction]
    rev = _neighbor_sets(edges, reverse)
    candidates = {
        c for n in own for c in rev.get(n, set()) if c != entity
    }
    items = []
    for c in candidates:
        cnb = nb.get(c, set())
        overlap = len(own & cnb)
        if overlap == 0:
            continue
        items.append({"id": c, "overlap": overlap,
                      "score": overlap / len(own | cnb)})
    items.sort(key=lambda r: (-r["score"], r["id"]))
    total = len(items)
    page = items[:top]
    ser = dict(zip(
        [r["id"] for r in page],
        serialize_entities(conn, [r["id"] for r in page], "compact"),
    ))
    return {
        "items": [
            {"entity": ser[r["id"]], "score": r["score"], "overlap": r["overlap"]}
            for r in page
        ],
        "total": total, "top": top,
    }


# --- welt_changes: Diff auf der Systemzeit-Achse ---------------------------------


def _parse_dt(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError(
            f"'{field}' ist kein gültiges ISO-Datetime: {value!r}"
        )


def changes(
    conn: psycopg.Connection,
    *,
    since: str,
    until: str | None = None,
    predicate_id: str | None = None,
    subject_id: str | None = None,
    kind: str = "all",
    limit: int = 200,
    output: str = "compact",
) -> dict[str, Any]:
    """Änderungen im Systemzeit-Fenster [since, until]: added = neue Zeilen
    (inkl. Supersession-Versionen), deprecated = Deprecation-Kopien (deren
    system_from ist der Deprecation-Zeitpunkt)."""
    _check_output(output)
    if kind not in ("added", "deprecated", "all"):
        raise ValidationError(
            f"Ungültiges kind '{kind}' (erlaubt: added, deprecated, all)"
        )
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until") if until is not None else None
    if until_dt is not None and since_dt > until_dt:
        raise ValidationError("'since' liegt nach 'until' — das Fenster ist leer.")
    eff_limit = _effective_limit(limit, output)
    if predicate_id:
        _check_predicates(conn, [predicate_id])

    params = {
        "since": since, "until": until,
        "predicate_id": predicate_id,
        "subject_id": _canonical(conn, subject_id) if subject_id else None,
        "kind": kind,
    }
    where = """
        WHERE s.system_from >= %(since)s
          AND (%(until)s::timestamptz IS NULL OR s.system_from <= %(until)s)
          AND (%(predicate_id)s::text IS NULL OR s.predicate_id = %(predicate_id)s)
          AND (%(subject_id)s::uuid IS NULL OR s.subject_id = %(subject_id)s)
          AND (%(kind)s = 'all'
               OR (%(kind)s = 'added' AND s.rank <> 'deprecated')
               OR (%(kind)s = 'deprecated' AND s.rank = 'deprecated'))
    """
    total = conn.execute(
        f"SELECT count(*) AS n FROM statement s {where}", params
    ).fetchone()["n"]
    rows = conn.execute(
        f"""{_STATEMENT_SELECT} {where}
            ORDER BY s.system_from DESC, s.id
            LIMIT {eff_limit}""",
        params,
    ).fetchall()
    for r in rows:
        r["change"] = "deprecated" if r["rank"] == "deprecated" else "added"
        r["changed_at"] = r["system_from"]
    return {"changes": serialize_statements(conn, rows, output),
            "total": total, "kind": kind, "limit": eff_limit}


# --- welt_sql: read-only Escape Hatch --------------------------------------------

SQL_VIEWS = ("v_entities", "v_statements", "v_qualifiers", "v_sources",
             "v_tool_log")

# Gefährliche/serverseitige Funktionen: Timing, Dateizugriff, Settings,
# Fremdzugriffe. Aggregen/String-Funktionen bleiben erlaubt.
_FORBIDDEN_FN = re.compile(
    r"^(pg_|current_setting|set_config|lo_|dblink|query_to_xml|xmltable|pgp_)",
)


def _validate_sql(query: str) -> None:
    try:
        statements = sqlglot.parse(query, dialect="postgres")
    except sqlglot.errors.ParseError as exc:
        raise ValidationError(f"SQL nicht parsebar: {exc.errors[0]['description']}")
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise ValidationError(
            "Genau ein Statement erlaubt — Multi-Statements (Semikolon) "
            "sind nicht zulässig."
        )
    stmt = statements[0]
    root = stmt
    while isinstance(root, exp.With):
        root = root.this
    if not isinstance(root, (exp.Select, exp.Union)):
        raise ValidationError(
            "Nur SELECT ist erlaubt — welt_sql ist strikt read-only. "
            "Schreiben geht ausschließlich über die Schreib-Tools "
            "(welt_commit_statement u. a.)."
        )
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in SQL_VIEWS:
            raise ValidationError(
                f"Tabelle/View '{table.name}' ist nicht erlaubt — nur die "
                f"Whitelist-Views: {', '.join(SQL_VIEWS)}."
            )
    for fn in stmt.find_all(exp.Func):
        name = (fn.name or fn.sql_name() or "").lower()
        if _FORBIDDEN_FN.match(name):
            raise ValidationError(f"Funktion '{name}' ist nicht erlaubt.")


def _view_columns_hint(conn: psycopg.Connection, query: str) -> str:
    """Spaltenlisten der in der Query referenzierten Whitelist-Views."""
    views = [v for v in SQL_VIEWS if re.search(rf"\b{v}\b", query, re.IGNORECASE)]
    parts = []
    for v in views:
        cols = conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = %s ORDER BY ordinal_position""", (v,),
        ).fetchall()
        parts.append(f"{v}({', '.join(c['column_name'] for c in cols)})")
    return " Verfügbare Spalten: " + "; ".join(parts) if parts else ""


def sql_query(
    conn: psycopg.Connection, *, query: str, limit: int = 500
) -> dict[str, Any]:
    """Geparstes, gegen die View-Whitelist geprüftes SELECT in einer
    read-only Transaktion mit 5-Sekunden-Timeout ausführen."""
    if limit < 1:
        raise ValidationError("limit muss >= 1 sein")
    limit = min(limit, IDS_MAX)
    _validate_sql(query)
    try:
        # psycopg öffnet die Transaktion implizit beim ersten execute — SET
        # TRANSACTION muss also VOR jeder Query laufen (Defense in depth
        # zusätzlich zum Parser): jede Schreiboperation scheitert hart.
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        cur = conn.execute(query)
        rows = cur.fetchmany(limit)
        truncated = cur.fetchone() is not None
    except psycopg.errors.QueryCanceled:
        raise ValidationError("Query-Timeout: Abbruch nach 5 Sekunden.")
    except psycopg.errors.UndefinedColumn as exc:
        # Prod-Muster: Agenten raten Spaltennamen und suchen dann in der Doku.
        # Die Spaltenlisten der referenzierten Views beenden die Schleife.
        primary = exc.diag.message_primary if exc.diag else str(exc)
        conn.rollback()
        raise ValidationError(f"SQL-Fehler: {primary}.{_view_columns_hint(conn, query)}")
    except psycopg.Error as exc:
        primary = exc.diag.message_primary if exc.diag else None
        raise ValidationError(f"SQL-Fehler: {primary or exc}")
    return {"rows": rows, "row_count": len(rows),
            "truncated": truncated, "limit": limit}
