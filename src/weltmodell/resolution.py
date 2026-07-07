"""Entity-Resolution / Dedup (Spec §7.2) — zweistufig.

1. Deterministisch: harte Keys (Prädikate mit identifying=true, z. B.
   email, wikidata_qid, account_uri) → exakter Match.
2. Fuzzy/Vektor: pgvector-Kosinus-Ähnlichkeit über Name+Kontext-Embedding,
   Schwelle → Merge-Kandidat.

merge_entity führt zusammen, ohne Statements zu verlieren — Provenance
beider Quellen bleibt (Invariante 4 sinngemäß: nichts wird gelöscht).
"""

from typing import Any

import psycopg

from .embeddings import get_embedder
from .entities import canonical_id, create_entity, get_entity
from .errors import ValidationError
from .registry import descendant_type_ids, get_predicate, get_type

VECTOR_CANDIDATE_THRESHOLD = 0.80  # Kosinus-Similarity → Merge-Kandidat
VECTOR_AUTO_MATCH_THRESHOLD = 0.93  # darüber: Pipeline nutzt Kandidat direkt


def resolve(
    conn: psycopg.Connection,
    *,
    type_id: str,
    label: str | None = None,
    identifiers: dict[str, str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Findet den kanonischen Anker für eine Entity-Beschreibung.

    Returns {"match": id|None, "method": ..., "candidates": [...]}.
    """
    if get_type(conn, type_id) is None:
        # Tippfehler-Typ würde sonst stumm null Kandidaten liefern — und der
        # Aufrufer legte im guten Glauben eine Dublette an.
        raise ValidationError(f"Unbekannter Typ '{type_id}'")
    warnings: list[str] = []
    # Stufe 1: deterministische Keys
    for pred_id, value in (identifiers or {}).items():
        pred = get_predicate(conn, pred_id)
        if pred is None or not pred["identifying"]:
            # Stilles Ignorieren hieße: der Aufrufer glaubt, dedupliziert zu
            # haben, und legt Dubletten an — laut sagen statt schlucken.
            warnings.append(
                f"identifier '{pred_id}' ist "
                + ("unbekannt" if pred is None else "kein identifying-Prädikat")
                + " — ignoriert"
            )
            continue
        row = conn.execute(
            """SELECT e.id FROM statement s
               JOIN entity e ON e.id = s.subject_id
               WHERE s.predicate_id = %s AND s.value_text = %s
                 AND s.system_to IS NULL AND s.rank <> 'deprecated'
                 AND e.merged_into IS NULL
               LIMIT 1""",
            (pred_id, value),
        ).fetchone()
        if row:
            hit: dict[str, Any] = {
                "match": str(row["id"]),
                "method": f"deterministic:{pred_id}",
                "candidates": [],
            }
            if warnings:
                hit["warnings"] = warnings
            return hit

    # Stufe 2: Kandidaten über das Label. Typ-Filter subtypfähig (Agent findet
    # Person/Organization). Erst exakte Label-Gleichheit (greift auch ohne
    # Embedding, z. B. Platform, und bei case-Varianten), dann Vektor-Ähnlichkeit.
    candidates: list[dict[str, Any]] = []
    if label:
        types = descendant_type_ids(conn, type_id)
        exact = conn.execute(
            """SELECT id, label, type_id FROM entity
               WHERE merged_into IS NULL AND lower(label) = lower(%s)
                 AND type_id = ANY(%s)
               LIMIT %s""",
            (label, types, limit),
        ).fetchall()
        candidates = [
            {**r, "id": str(r["id"]), "similarity": 1.0} for r in exact
        ]
        seen = {c["id"] for c in candidates}
        embedding = get_embedder().embed(label)
        rows = conn.execute(
            """SELECT id, label, type_id,
                      1 - (embedding <=> %s::vector) AS similarity
               FROM entity
               WHERE merged_into IS NULL AND embedding IS NOT NULL
                 AND type_id = ANY(%s)
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (embedding, types, embedding, limit),
        ).fetchall()
        candidates += [
            {**r, "id": str(r["id"]), "similarity": float(r["similarity"])}
            for r in rows
            if r["similarity"] >= VECTOR_CANDIDATE_THRESHOLD
            and str(r["id"]) not in seen
        ]

    result: dict[str, Any] = {"match": None, "method": None, "candidates": candidates}
    if warnings:
        result["warnings"] = warnings
    return result


def get_or_create_entity(
    conn: psycopg.Connection,
    *,
    type_id: str,
    label: str | None = None,
    identifiers: dict[str, str] | None = None,
    source_ids: list[str] | None = None,
) -> tuple[str, bool]:
    """RESOLVE-Stufe der Pipeline (§7): Match zurückgeben oder Anker anlegen.

    Identifying-Statements werden beim Anlegen mitgeschrieben (mit Provenance),
    damit spätere Läufe deterministisch matchen. Returns (entity_id, created).
    """
    res = resolve(conn, type_id=type_id, label=label, identifiers=identifiers)
    if res["match"]:
        return res["match"], False
    if res["candidates"] and res["candidates"][0]["similarity"] >= VECTOR_AUTO_MATCH_THRESHOLD:
        return res["candidates"][0]["id"], False

    entity = create_entity(conn, type_id=type_id, label=label)
    entity_id = str(entity["id"])
    if identifiers and source_ids:
        from .statements import commit_statement  # zyklischen Import vermeiden

        for pred_id, value in identifiers.items():
            pred = get_predicate(conn, pred_id)
            if pred is None:
                continue
            commit_statement(
                conn,
                subject_id=entity_id,
                predicate_id=pred_id,
                value={"type": "string", "text": value},
                source_ids=source_ids,
            )
    return entity_id, True


def merge_entity(
    conn: psycopg.Connection, source_id: str, target_id: str
) -> dict[str, Any]:
    """Führt source in target zusammen, ohne Statements zu verlieren (§7.2)."""
    source_id = canonical_id(conn, source_id)
    target_id = canonical_id(conn, target_id)
    if source_id == target_id:
        raise ValidationError("Entity kann nicht mit sich selbst gemerged werden")
    source, target = get_entity(conn, source_id), get_entity(conn, target_id)
    if source["type_id"] != target["type_id"]:
        raise ValidationError(
            f"Typ-Konflikt beim Merge: '{source['type_id']}' vs. "
            f"'{target['type_id']}'"
        )

    moved_subject = conn.execute(
        "UPDATE statement SET subject_id = %s WHERE subject_id = %s RETURNING id",
        (target_id, source_id),
    ).fetchall()
    moved_object = conn.execute(
        "UPDATE statement SET object_id = %s WHERE object_id = %s RETURNING id",
        (target_id, source_id),
    ).fetchall()
    # Merge-Artefakte: Statements, die durchs Umbiegen selbstreferenziell
    # wurden (Dublette kannte/folgte dem Original), sind keine Fakten über
    # die Welt — transaktionszeitlich schließen (Historie bleibt), nie in
    # der Current View zeigen. Nur bewegte Zeilen, nie unabhängige Self-Loops.
    moved_ids = [r["id"] for r in moved_subject + moved_object]
    self_loops = 0
    if moved_ids:
        self_loops = conn.execute(
            """UPDATE statement SET system_to = now()
               WHERE id = ANY(%s) AND subject_id = object_id
                 AND system_to IS NULL""",
            (moved_ids,),
        ).rowcount
    conn.execute(
        "UPDATE qualifier SET object_id = %s WHERE object_id = %s",
        (target_id, source_id),
    )
    conn.execute(
        "UPDATE entity SET merged_into = %s WHERE id = %s", (target_id, source_id)
    )
    return {
        "merged": source_id,
        "into": target_id,
        "statements_moved": len(moved_ids),
        "self_loops_closed": self_loops,
    }
