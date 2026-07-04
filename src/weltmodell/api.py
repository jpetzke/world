"""FastAPI-Actions — der einzige Schreibweg ins Substrat (Spec §7.1).

Registry-Gate, Statement-Commit, Merge, Suche und Traversierung als
erzwungene Code-Pfade, nicht Konvention. Gilt für Menschen- und
LLM-Writes gleichermaßen (Invariante 2).
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import auth, files, follower_import, pipeline, queries, registry, resolution, statements
from .auth import require_auth
from .config import get_session_secret, is_prod
from .db import get_conn, run_migrations
from .entities import create_entity, get_entity
from .errors import NotFoundError, RegistryError, ValidationError

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# Uploads landen komplett im RAM (await file.read()) — Deckel gegen OOM/DoS.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB

_PROD = is_prod()

_CSP = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "object-src 'none'",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",  # React setzt style-Attribute
        "script-src 'self'",
        "connect-src 'self'",
        "font-src 'self'",
    ]
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield


# Docs/OpenAPI in Prod aus — kein unnötiges Aufdecken der API-Fläche.
app = FastAPI(
    title="Weltmodell",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if _PROD else "/docs",
    redoc_url=None if _PROD else "/redoc",
    openapi_url=None if _PROD else "/openapi.json",
)

# Signierte Server-Session; HttpOnly (Starlette-Default), Secure in Prod,
# SameSite=Strict → CSRF-fest ohne separates Token.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    session_cookie="wm_session",
    https_only=_PROD,
    same_site="strict",
    max_age=14 * 24 * 3600,
)

# CORS nur im Dev (Vite auf anderem Port). In Prod läuft alles same-origin.
if not _PROD:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = _CSP
    if _PROD:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Unauthentifizierter Liveness-Check für Coolify/Traefik."""
    conn = get_conn()
    try:
        conn.execute("SELECT 1")
        return {"status": "ok"}
    finally:
        conn.close()


router = APIRouter()


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
    max_depth: int = 1
    predicates: list[str] | None = None
    max_nodes: int = 400


class FollowerRow(BaseModel):
    username: str
    display_name: str | None = None


class FollowerListPreviewPayload(BaseModel):
    owner_entity_id: str
    direction: Literal["followers", "following"]
    rows: list[FollowerRow] = Field(min_length=1)


class FollowerListCommitPayload(FollowerListPreviewPayload):
    observed_at: str | None = None
    agent: str = "ui:follower-import"


class IngestPayload(BaseModel):
    activity: str
    agent: str
    raw: dict[str, Any]
    url: str | None = None
    retrieved_at: str | None = None
    run_pipeline: bool = True
    extractor: str = "rule-based"  # 'rule-based' | 'llm' (OpenRouter)


# --- Endpoints ---------------------------------------------------------------


@router.get("/health")
def health(conn=Depends(db)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@router.get("/registry/types")
def get_types(conn=Depends(db)):
    return registry.list_types(conn)


@router.get("/registry/interfaces")
def get_interfaces(conn=Depends(db)):
    return registry.list_interfaces(conn)


@router.get("/registry/predicates")
def get_predicates(conn=Depends(db)):
    return registry.list_predicates(conn)


@router.get("/registry/vocabulary")
def get_vocabulary(conn=Depends(db)):
    """Das erlaubte Vokabular für LLM-Extraktoren (§7.1)."""
    return registry.vocabulary(conn)


@router.get("/registry/proposals")
def get_proposals(status: str = "pending", conn=Depends(db)):
    return registry.list_proposals(conn, status)


@router.post("/registry/proposals/types", status_code=201)
def post_type_proposal(payload: TypeProposal, conn=Depends(db)):
    return registry.propose_type(conn, **payload.model_dump())


@router.post("/registry/proposals/predicates", status_code=201)
def post_predicate_proposal(payload: PredicateProposal, conn=Depends(db)):
    return registry.propose_predicate(conn, **payload.model_dump())


@router.post("/registry/proposals/types/{proposal_id}/approve")
def approve_type(proposal_id: str, conn=Depends(db)):
    return registry.approve_type(conn, proposal_id)


@router.post("/registry/proposals/predicates/{proposal_id}/approve")
def approve_predicate(proposal_id: str, conn=Depends(db)):
    return registry.approve_predicate(conn, proposal_id)


@router.post("/registry/proposals/types/{proposal_id}/reject")
def reject_type(proposal_id: str, conn=Depends(db)):
    return registry.reject_proposal(conn, "proposed_type", proposal_id)


@router.post("/registry/proposals/predicates/{proposal_id}/reject")
def reject_predicate(proposal_id: str, conn=Depends(db)):
    return registry.reject_proposal(conn, "proposed_predicate", proposal_id)


@router.post("/entities", status_code=201)
def post_entity(payload: EntityCreate, conn=Depends(db)):
    return create_entity(conn, **payload.model_dump())


@router.get("/entities/{entity_id}")
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


@router.get("/entities/{entity_id}/timeline")
def get_entity_timeline(entity_id: str, conn=Depends(db)):
    return queries.entity_timeline(conn, entity_id)


@router.post("/entities/{entity_id}/merge")
def post_merge(entity_id: str, payload: MergePayload, conn=Depends(db)):
    return resolution.merge_entity(conn, entity_id, payload.target_id)


@router.post("/resolve")
def post_resolve(payload: ResolvePayload, conn=Depends(db)):
    return resolution.resolve(conn, **payload.model_dump())


@router.post("/sources", status_code=201)
def post_source(payload: SourceCreate, conn=Depends(db)):
    return pipeline.ingest_document(
        conn, raw=payload.raw or {}, url=payload.url,
        activity=payload.activity, agent=payload.agent,
        retrieved_at=payload.retrieved_at,
    )


@router.post("/statements", status_code=201)
def post_statement(payload: StatementCreate, conn=Depends(db)):
    data = payload.model_dump()
    data["qualifiers"] = [q for q in data["qualifiers"]]
    return statements.commit_statement(conn, **data)


@router.post("/statements/{statement_id}/deprecate")
def post_deprecate(statement_id: str, payload: DeprecatePayload, conn=Depends(db)):
    return statements.deprecate_statement(conn, statement_id, valid_to=payload.valid_to)


@router.post("/statements/{statement_id}/rank")
def post_rank(statement_id: str, payload: RankPayload, conn=Depends(db)):
    if payload.rank not in ("preferred", "normal", "deprecated"):
        raise ValidationError(f"Ungültiger Rank '{payload.rank}'")
    return statements.supersede_statement(conn, statement_id, rank=payload.rank)


@router.get("/search")
def get_search(q: str, type_id: str | None = None, limit: int = 10, conn=Depends(db)):
    return queries.semantic_search(conn, q, type_id=type_id, limit=limit)


@router.post("/query/traverse")
def post_traverse(payload: TraversePayload, conn=Depends(db)):
    return queries.neighborhood(
        conn, payload.start_id, max_depth=payload.max_depth,
        predicates=payload.predicates, max_nodes=min(payload.max_nodes, 2000),
    )


@router.get("/stats")
def get_stats(conn=Depends(db)):
    return queries.stats(conn)


@router.get("/graph")
def get_graph(max_nodes: int = 400, conn=Depends(db)):
    return queries.graph_snapshot(conn, max_nodes=min(max_nodes, 2000))


@router.get("/entities")
def get_entities(
    type_id: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn=Depends(db),
):
    return queries.list_entities(conn, type_id=type_id, q=q,
                                 limit=min(limit, 200), offset=offset)


@router.get("/sources")
def get_sources(limit: int = 50, offset: int = 0, conn=Depends(db)):
    return queries.list_sources(conn, limit=min(limit, 200), offset=offset)


@router.post("/sources/upload", status_code=201)
async def post_source_upload(
    file: UploadFile,
    activity: str = Form("upload"),
    url: str | None = Form(None),
    conn=Depends(db),
):
    """Original-Datei hochladen: legt eine Quelle an und archiviert das
    Original binär in der DB (1:1). Keine Extraktion — reines Archiv (§5)."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu groß (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB).",
        )
    doc = pipeline.ingest_document(
        conn, raw={}, url=url, activity=activity,
        agent=file.filename or "upload",
    )
    meta = files.store_source_file(
        conn,
        source_id=str(doc["id"]),
        filename=file.filename or "unbenannt",
        mime=file.content_type or "application/octet-stream",
        data=data,
    )
    return {"source": doc, "file": meta}


@router.get("/sources/{source_id}/file")
def get_source_file_download(source_id: str, conn=Depends(db)):
    row = files.get_source_file(conn, source_id)
    return Response(
        content=bytes(row["data"]),
        media_type=row["mime"],
        headers={
            "Content-Disposition": f'attachment; filename="{row["filename"]}"'
        },
    )


@router.get("/sources/{source_id}")
def get_source_detail(source_id: str, conn=Depends(db)):
    return queries.get_source(conn, source_id)


@router.post("/ingest/follower-list/preview")
def post_follower_list_preview(payload: FollowerListPreviewPayload, conn=Depends(db)):
    return follower_import.preview_follower_list(
        conn, rows=[r.model_dump() for r in payload.rows],
        owner_entity_id=payload.owner_entity_id, direction=payload.direction,
    )


@router.post("/ingest/follower-list/commit", status_code=201)
def post_follower_list_commit(payload: FollowerListCommitPayload, conn=Depends(db)):
    return follower_import.commit_follower_list(
        conn, rows=[r.model_dump() for r in payload.rows],
        owner_entity_id=payload.owner_entity_id, direction=payload.direction,
        observed_at=payload.observed_at, agent=payload.agent,
    )


@router.post("/ingest", status_code=201)
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


# --- Wiring: API unter /api, Frontend-Build als SPA auf / -------------------

# Auth-Routen ungeschützt (Login/Logout/Me). Alles andere unter /api verlangt
# eine gültige Session (Invariante 2 bleibt: der einzige Schreibweg ist die API,
# jetzt zusätzlich hinter Auth).
app.include_router(auth.router, prefix="/api")
app.include_router(router, prefix="/api", dependencies=[Depends(require_auth)])

if FRONTEND_DIST.exists():

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"Unbekannte API-Route /{path}")
        candidate = (FRONTEND_DIST / path).resolve()
        if (
            path
            and candidate.is_relative_to(FRONTEND_DIST)
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
