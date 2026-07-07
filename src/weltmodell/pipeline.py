"""KI-Fill-Pipeline (Spec §7): INGEST → EXTRACT → RESOLVE → VALIDATE → COMMIT.

Jede Stufe schreibt Provenance. Der Extraktor schreibt nie direkt (§7.1):
er bekommt das Registry-Vokabular und emittiert Kandidaten-Statements plus
proposed_predicate/proposed_type für alles, was nicht mappbar ist — die
gehen ins Review-Gate.

Der LLM-Extraktor ist austauschbar (Extractor-Protokoll); mitgeliefert ist
ein regelbasierter Demo-Extraktor für strukturierte Social-Profile.
INFER (§7.3) ist bewusst später — origin='inferred' trägt es ohne Redesign.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

from . import registry
from .errors import RegistryError, ValidationError
from .resolution import get_or_create_entity
from .statements import commit_statement


@dataclass
class EntityRef:
    """Beschreibung einer Entity vor der Resolution."""

    type_id: str
    label: str | None = None
    identifiers: dict[str, str] = field(default_factory=dict)


@dataclass
class CandidateStatement:
    subject: EntityRef
    predicate_id: str
    value: dict[str, Any] | EntityRef  # Literal-Wert oder Entity-Referenz
    confidence: float = 1.0
    valid_from: Any = None
    valid_to: Any = None
    qualifiers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExtractionResult:
    statements: list[CandidateStatement] = field(default_factory=list)
    proposed_predicates: list[dict[str, Any]] = field(default_factory=list)
    proposed_types: list[dict[str, Any]] = field(default_factory=list)
    # fehlgeformte Extraktor-Items (z. B. LLM-Output ohne subject/predicate):
    # werden übersprungen und im Pipeline-Report als rejected ausgewiesen
    malformed: list[dict[str, Any]] = field(default_factory=list)


class Extractor(Protocol):
    def extract(self, raw: dict, vocabulary: dict) -> ExtractionResult: ...


class RuleBasedExtractor:
    """Demo-Extraktor für raw-Dokumente der Form kind='social_profile'.

    Minimalmodell (§9): nur Person und SocialMediaAccount. Versteht:
    name, email, aliases[], knows[{name, since}],
    accounts[{platform, handle, uri, follows[uri]}].
    Unbekannte Top-Level-Felder werden NICHT geschrieben, sondern als
    proposed_predicate ins Gate gegeben (§7.1).
    """

    KNOWN_KEYS = {"kind", "name", "email", "aliases", "knows", "accounts"}

    def extract(self, raw: dict, vocabulary: dict) -> ExtractionResult:
        result = ExtractionResult()
        if raw.get("kind") != "social_profile" or not raw.get("name"):
            return result

        identifiers = {"email": raw["email"]} if raw.get("email") else {}
        person = EntityRef("Person", label=raw["name"], identifiers=identifiers)

        result.statements.append(
            CandidateStatement(person, "name",
                               {"type": "string", "text": raw["name"]},
                               confidence=0.95)
        )

        if raw.get("email"):
            result.statements.append(
                CandidateStatement(person, "email",
                                   {"type": "string", "text": raw["email"]},
                                   confidence=0.95)
            )

        for alias in raw.get("aliases", []):
            result.statements.append(
                CandidateStatement(person, "alias",
                                   {"type": "string", "text": alias},
                                   confidence=0.9)
            )

        for other in raw.get("knows", []):
            other_name = other.get("name") if isinstance(other, dict) else other
            if not other_name:
                continue
            # „seit" ist Valid-Time der Beziehung (§3), kein Qualifier
            since = other.get("since") if isinstance(other, dict) else None
            result.statements.append(
                CandidateStatement(person, "knows",
                                   EntityRef("Person", label=other_name),
                                   confidence=0.85, valid_from=since)
            )

        for acc in raw.get("accounts", []):
            handle, platform = acc.get("handle"), acc.get("platform")
            uri = acc.get("uri") or (f"{platform}:{handle}" if platform and handle else None)
            account = EntityRef(
                # Bare handle als Bezeichner — deckungsgleich mit dem handle-
                # Statement (label_predicate) und mit follower_import. Ein „@" nur
                # im Cache wäre eine zweite Wahrheit (Inv. 1); Deko gehört ins UI.
                "SocialMediaAccount",
                label=handle if handle else uri,
                identifiers={"account_uri": uri} if uri else {},
            )
            result.statements.append(
                CandidateStatement(person, "owns_account", account, confidence=0.9)
            )
            if handle:
                result.statements.append(
                    CandidateStatement(account, "handle",
                                       {"type": "string", "text": handle},
                                       confidence=0.9)
                )
            if platform:
                # platform ist eine kontrollierte Entity (Platform), kein Freitext.
                # ponytail: resolve-or-create per Label kanonisiert nicht gegen den
                # geseedeten Set („linkedin" ≠ „LinkedIn") — Platform-Dedup ist später
                # Sache der Entity-Resolution, nicht des Extraktors.
                result.statements.append(
                    CandidateStatement(account, "platform",
                                       EntityRef("Platform", label=platform),
                                       confidence=0.9)
                )
            for target_uri in acc.get("follows", []):
                target = EntityRef(
                    "SocialMediaAccount", label=target_uri,
                    identifiers={"account_uri": target_uri},
                )
                result.statements.append(
                    CandidateStatement(account, "follows", target, confidence=0.8)
                )

        # Restliche Felder: auf existierende Prädikate mappen — oder Gate (§7.1).
        # Nach Approve eines Proposals mappt der nächste Lauf automatisch (Flywheel).
        known_predicates = {p["id"] for p in vocabulary["predicates"]}
        for key, value in raw.items():
            if key in self.KNOWN_KEYS:
                continue
            range_kind = "number" if isinstance(value, (int, float)) else "string"
            if key in known_predicates:
                result.statements.append(
                    CandidateStatement(
                        person, key,
                        {"type": range_kind, "number": value}
                        if range_kind == "number"
                        else {"type": "string", "text": str(value)},
                        confidence=0.7,
                    )
                )
                continue
            result.proposed_predicates.append({
                "predicate_id": key,
                "label": key.replace("_", " "),
                "domain_type": "Person",
                "range_kind": range_kind,
                "cardinality": "1:n",
                "rationale": f"Unbekanntes Feld im source_document: {key}={value!r}",
            })
        return result


def ingest_document(
    conn: psycopg.Connection,
    *,
    raw: dict,
    url: str | None = None,
    activity: str,
    agent: str,
    retrieved_at: Any = None,
) -> dict:
    """INGEST (§7 Stufe 1): rohes source_document speichern."""
    import json

    return conn.execute(
        """INSERT INTO source_document (url, retrieved_at, activity, agent, raw)
           VALUES (%s, COALESCE(%s::timestamptz, now()), %s, %s, %s::jsonb)
           RETURNING *""",
        (url, retrieved_at, activity, agent, json.dumps(raw)),
    ).fetchone()


def run_pipeline(
    conn: psycopg.Connection,
    *,
    source_id: str,
    extractor: Extractor | None = None,
    agent: str = "pipeline:rule-based",
) -> dict[str, Any]:
    """EXTRACT → RESOLVE → VALIDATE → COMMIT für ein source_document."""
    doc = conn.execute(
        "SELECT * FROM source_document WHERE id = %s", (source_id,)
    ).fetchone()
    if doc is None:
        raise ValidationError(f"source_document {source_id} nicht gefunden")

    extractor = extractor or RuleBasedExtractor()
    vocab = registry.vocabulary(conn)
    extraction = extractor.extract(doc["raw"] or {}, vocab)

    resolved_refs: dict[int, str] = {}  # id(EntityRef) → entity_id, stabil pro Lauf
    created_entities: list[str] = []

    def resolve_ref(ref: EntityRef) -> str:
        key = id(ref)
        if key not in resolved_refs:
            entity_id, created = get_or_create_entity(
                conn,
                type_id=ref.type_id,
                label=ref.label,
                identifiers=ref.identifiers,
                source_ids=[str(source_id)],
            )
            resolved_refs[key] = entity_id
            if created:
                created_entities.append(entity_id)
        return resolved_refs[key]

    committed, rejected = [], []
    for item in extraction.malformed:
        rejected.append({"predicate": item.get("predicate_id"),
                         "problems": [item.get("problem", "fehlgeformtes Item")]})
    for i, cand in enumerate(extraction.statements):
        # Savepoint pro Kandidat: ein fehlerhafter Kandidat (auch ein
        # DB-Fehler, z. B. unparsebares LLM-Datum) landet in rejected und
        # rollt nur sich selbst zurück — ohne Savepoint wäre die Transaktion
        # abgebrochen und ALLE Folge-Kandidaten scheiterten mit. Der
        # Ref-Cache wird mit zurückgesetzt (im Savepoint angelegte Entities
        # existieren nach dem Rollback nicht mehr).
        refs_before = dict(resolved_refs)
        created_before = list(created_entities)
        conn.execute(f"SAVEPOINT cand_{i}")
        try:
            subject_id = resolve_ref(cand.subject)
            value = (
                {"type": "entity", "object_id": resolve_ref(cand.value)}
                if isinstance(cand.value, EntityRef)
                else cand.value
            )
            row = commit_statement(
                conn,
                subject_id=subject_id,
                predicate_id=cand.predicate_id,
                value=value,
                source_ids=[str(source_id)],
                confidence=cand.confidence,
                valid_from=cand.valid_from,
                valid_to=cand.valid_to,
                qualifiers=cand.qualifiers,
            )
            conn.execute(f"RELEASE SAVEPOINT cand_{i}")
            committed.append(str(row["id"]))
        except (ValidationError, psycopg.Error) as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT cand_{i}")
            resolved_refs.clear()
            resolved_refs.update(refs_before)
            created_entities[:] = created_before
            problems = (
                exc.problems
                if isinstance(exc, ValidationError) and exc.problems
                else [str(exc)]
            )
            rejected.append({"predicate": cand.predicate_id, "problems": problems})

    proposals = []
    for prop in extraction.proposed_predicates:
        try:
            row = registry.propose_predicate(conn, proposed_by=agent, **prop)
            proposals.append({"kind": "predicate", "id": str(row["id"]),
                              "predicate_id": row["predicate_id"]})
        except RegistryError as exc:
            rejected.append({"proposal": prop.get("predicate_id"),
                             "problems": [str(exc)]})
    for prop in extraction.proposed_types:
        try:
            row = registry.propose_type(conn, proposed_by=agent, **prop)
            proposals.append({"kind": "type", "id": str(row["id"]),
                              "type_id": row["type_id"]})
        except RegistryError as exc:
            rejected.append({"proposal": prop.get("type_id"),
                             "problems": [str(exc)]})

    return {
        "source_id": str(source_id),
        "committed": committed,
        "rejected": rejected,
        "proposals": proposals,
        "entities_created": created_entities,
    }
