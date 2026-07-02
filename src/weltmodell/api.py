"""FastAPI-Actions — der einzige Schreibweg ins Substrat (Spec §7.1).

Registry-Gate, Statement-Commit, Merge, Suche und Traversierung als
erzwungene Code-Pfade, nicht Konvention. Gilt für Menschen- und
LLM-Writes gleichermaßen (Invariante 2).
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import pipeline, queries, registry, resolution, statements
from .db import get_conn, run_migrations
from .entities import create_entity, get_entity
from .errors import NotFoundError, RegistryError, ValidationError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield


app = FastAPI(title="Weltmodell", version="0.1.0", lifespan=lifespan)


def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.exception_handler(ValidationError)
async def _validation(_, exc: ValidationError):
    raise HTTPException(status_code=422, detail=exc.problems)


@app.exception_handler(RegistryError)
async def _registry(_, exc: RegistryError):
    raise HTTPException(status_code=409, detail=str(exc))


@app.exception_handler(NotFoundError)
async def _not_found(_, exc: NotFoundError):
    raise HTTPException(status_code=404, detail=str(exc))


# --- Payloads ----------------------------------------------------------------


class EntityCreate(BaseModel):
    type_id: str
    label: str | None = None
    embed_text: str | None = None


class SourceCreate(BaseModel):
    activity: str
    agent: str
    url: str | None = None
    raw: dict[str, Any] | None = None
    retrieved_at: str | None = None


class QualifierPayload(BaseModel):
    predicate_id: str
    value: dict[str, Any]


class StatementCreate(BaseModel):
    subject_id: str
    predicate_id: str
    value: dict[str, Any]
    source_ids: list[str] = Field(min_length=1)
    rank: str = "normal"
    confidence: float = 1.0
    origin: str = "asserted"
    valid_from: str | None = None
    valid_to: str | None = None
    qualifiers: list[QualifierPayload] = []


class DeprecatePayload(BaseModel):
    valid_to: str | None = None


class RankPayload(BaseModel):
    rank: str


class TypeProposal(BaseModel):
    type_id: str
    parent_id: str
    kind: str
    label: str
    interfaces: list[str] = []
    wikidata_qid: str | None = None
    rationale: str | None = None
    proposed_by: str


class PredicateProposal(BaseModel):
    predicate_id: str
    label: str
    range_kind: str
    domain_type: str | None = None
    domain_interface: str | None = None
    range_type: str | None = None
    cardinality: str | None = None
    inverse_id: str | None = None
    wikidata_pid: str | None = None
    schema_org: str | None = None
    rationale: str | None = None
    proposed_by: str


class MergePayload(BaseModel):
    target_id: str


class ResolvePayload(BaseModel):
    type_id: str
    label: str | None = None
    identifiers: dict[str, str] = {}


class TraversePayload(BaseModel):
    start_id: str
    max_depth: int = 3
    predicates: list[str] | None = None


class IngestPayload(BaseModel):
    activity: str
    agent: str
    raw: dict[str, Any]
    url: str | None = None
    retrieved_at: str | None = None
    run_pipeline: bool = True
    extractor: str = "rule-based"  # 'rule-based' | 'llm' (OpenRouter)


# --- Endpoints ---------------------------------------------------------------


@app.get("/health")
def health(conn=Depends(db)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/registry/types")
def get_types(conn=Depends(db)):
    return registry.list_types(conn)


@app.get("/registry/interfaces")
def get_interfaces(conn=Depends(db)):
    return registry.list_interfaces(conn)


@app.get("/registry/predicates")
def get_predicates(conn=Depends(db)):
    return registry.list_predicates(conn)


@app.get("/registry/vocabulary")
def get_vocabulary(conn=Depends(db)):
    """Das erlaubte Vokabular für LLM-Extraktoren (§7.1)."""
    return registry.vocabulary(conn)


@app.get("/registry/proposals")
def get_proposals(status: str = "pending", conn=Depends(db)):
    return registry.list_proposals(conn, status)


@app.post("/registry/proposals/types", status_code=201)
def post_type_proposal(payload: TypeProposal, conn=Depends(db)):
    return registry.propose_type(conn, **payload.model_dump())


@app.post("/registry/proposals/predicates", status_code=201)
def post_predicate_proposal(payload: PredicateProposal, conn=Depends(db)):
    return registry.propose_predicate(conn, **payload.model_dump())


@app.post("/registry/proposals/types/{proposal_id}/approve")
def approve_type(proposal_id: str, conn=Depends(db)):
    return registry.approve_type(conn, proposal_id)


@app.post("/registry/proposals/predicates/{proposal_id}/approve")
def approve_predicate(proposal_id: str, conn=Depends(db)):
    return registry.approve_predicate(conn, proposal_id)


@app.post("/registry/proposals/types/{proposal_id}/reject")
def reject_type(proposal_id: str, conn=Depends(db)):
    return registry.reject_proposal(conn, "proposed_type", proposal_id)


@app.post("/registry/proposals/predicates/{proposal_id}/reject")
def reject_predicate(proposal_id: str, conn=Depends(db)):
    return registry.reject_proposal(conn, "proposed_predicate", proposal_id)


@app.post("/entities", status_code=201)
def post_entity(payload: EntityCreate, conn=Depends(db)):
    return create_entity(conn, **payload.model_dump())


@app.get("/entities/{entity_id}")
def get_entity_view(
    entity_id: str,
    system_at: str | None = None,
    valid_at: str | None = None,
    include_deprecated: bool = False,
    conn=Depends(db),
):
    return queries.entity_view(
        conn, entity_id, system_at=system_at, valid_at=valid_at,
        include_deprecated=include_deprecated,
    )


@app.post("/entities/{entity_id}/merge")
def post_merge(entity_id: str, payload: MergePayload, conn=Depends(db)):
    return resolution.merge_entity(conn, entity_id, payload.target_id)


@app.post("/resolve")
def post_resolve(payload: ResolvePayload, conn=Depends(db)):
    return resolution.resolve(conn, **payload.model_dump())


@app.post("/sources", status_code=201)
def post_source(payload: SourceCreate, conn=Depends(db)):
    return pipeline.ingest_document(
        conn, raw=payload.raw or {}, url=payload.url,
        activity=payload.activity, agent=payload.agent,
        retrieved_at=payload.retrieved_at,
    )


@app.post("/statements", status_code=201)
def post_statement(payload: StatementCreate, conn=Depends(db)):
    data = payload.model_dump()
    data["qualifiers"] = [q for q in data["qualifiers"]]
    return statements.commit_statement(conn, **data)


@app.post("/statements/{statement_id}/deprecate")
def post_deprecate(statement_id: str, payload: DeprecatePayload, conn=Depends(db)):
    return statements.deprecate_statement(conn, statement_id, valid_to=payload.valid_to)


@app.post("/statements/{statement_id}/rank")
def post_rank(statement_id: str, payload: RankPayload, conn=Depends(db)):
    if payload.rank not in ("preferred", "normal", "deprecated"):
        raise ValidationError(f"Ungültiger Rank '{payload.rank}'")
    return statements.supersede_statement(conn, statement_id, rank=payload.rank)


@app.get("/search")
def get_search(q: str, type_id: str | None = None, limit: int = 10, conn=Depends(db)):
    return queries.semantic_search(conn, q, type_id=type_id, limit=limit)


@app.post("/query/traverse")
def post_traverse(payload: TraversePayload, conn=Depends(db)):
    return queries.traverse(
        conn, payload.start_id, max_depth=payload.max_depth,
        predicates=payload.predicates,
    )


@app.post("/ingest", status_code=201)
def post_ingest(payload: IngestPayload, conn=Depends(db)):
    doc = pipeline.ingest_document(
        conn, raw=payload.raw, url=payload.url, activity=payload.activity,
        agent=payload.agent, retrieved_at=payload.retrieved_at,
    )
    report = None
    if payload.run_pipeline:
        extractor = None
        if payload.extractor == "llm":
            from .llm import LLMExtractor

            extractor = LLMExtractor()
        elif payload.extractor != "rule-based":
            raise ValidationError(f"Unbekannter Extraktor '{payload.extractor}'")
        report = pipeline.run_pipeline(conn, source_id=str(doc["id"]),
                                       agent=payload.agent, extractor=extractor)
    return {"source": doc, "pipeline": report}
