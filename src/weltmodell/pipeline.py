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


class Extractor(Protocol):
    def extract(self, raw: dict, vocabulary: dict) -> ExtractionResult: ...


class RuleBasedExtractor:
    """Demo-Extraktor für raw-Dokumente der Form kind='social_profile'.

    Versteht: name, email, employer{name, role, hours, since}, partner,
    accounts[{platform, handle, uri}], mentions[{text, date, url}].
    Unbekannte Top-Level-Felder werden NICHT geschrieben, sondern als
    proposed_predicate ins Gate gegeben (§7.1).
    """

    KNOWN_KEYS = {"kind", "name", "email", "employer", "partner", "accounts", "mentions"}

    def extract(self, raw: dict, vocabulary: dict) -> ExtractionResult:
        result = ExtractionResult()
        if raw.get("kind") != "social_profile" or not raw.get("name"):
            return result

        identifiers = {"email": raw["email"]} if raw.get("email") else {}
        person = EntityRef("Person", label=raw["name"], identifiers=identifiers)

        if raw.get("email"):
            result.statements.append(
                CandidateStatement(person, "email",
                                   {"type": "string", "text": raw["email"]},
                                   confidence=0.95)
            )

        if emp := raw.get("employer"):
            qualifiers = []
            if emp.get("role"):
                qualifiers.append({"predicate_id": "role",
                                   "value": {"type": "string", "text": emp["role"]}})
            if emp.get("hours") is not None:
                qualifiers.append({"predicate_id": "hours",
                                   "value": {"type": "number", "number": emp["hours"]}})
            result.statements.append(
                CandidateStatement(person, "works_at",
                                   EntityRef("Organization", label=emp["name"]),
                                   confidence=0.9, valid_from=emp.get("since"),
                                   qualifiers=qualifiers)
            )

        if partner := raw.get("partner"):
            result.statements.append(
                CandidateStatement(person, "romantic_partner_of",
                                   EntityRef("Person", label=partner),
                                   confidence=0.85)
            )

        for acc in raw.get("accounts", []):
            handle, platform = acc.get("handle"), acc.get("platform")
            uri = acc.get("uri") or (f"{platform}:{handle}" if platform and handle else None)
            account = EntityRef(
                "Account",
                label=f"@{handle}" if handle else uri,
                identifiers={"account_uri": uri} if uri else {},
            )
            result.statements.append(
                CandidateStatement(person, "owns_account", account, confidence=0.9)
            )
            for pred, val in (("handle", handle), ("platform", platform)):
                if val:
                    result.statements.append(
                        CandidateStatement(account, pred,
                                           {"type": "string", "text": val},
                                           confidence=0.9)
                    )

        for m in raw.get("mentions", []):
            snippet = m.get("text", "")
            mention = EntityRef("Mention", label=snippet[:80])
            result.statements.append(
                CandidateStatement(mention, "mentions", person,
                                   confidence=0.8, valid_from=m.get("date"))
            )
            if snippet:
                result.statements.append(
                    CandidateStatement(mention, "text",
                                       {"type": "string", "text": snippet},
                                       confidence=0.8, valid_from=m.get("date"))
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
    for cand in extraction.statements:
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
            committed.append(str(row["id"]))
        except ValidationError as exc:
            rejected.append({"predicate": cand.predicate_id, "problems": exc.problems})

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
