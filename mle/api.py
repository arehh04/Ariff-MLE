"""FastAPI service: similar companies, company detail, probes. Contract: docs/04-api.md.

Visibility (decided): the `X-Role` header is a stub, not authentication. `portal` (default) never sees struck-off
companies, neither in results nor as the requested company (404); `analyst` sees them and their `status`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from starlette.exceptions import HTTPException as StarletteHTTPException

from mle import db, repository
from mle.config import Settings, get_settings
from mle.normalize import MALAYSIA, normalize_registration_number, normalize_state

log = logging.getLogger("api")
PROBLEM_BASE = "https://onecredit.example/problems/"
Role = Literal["portal", "analyst"]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CompanyCard(_Camel):
    registration_number: str
    company_name: str | None
    nature_of_business: str | None
    state: str | None
    industry: str | None
    status: str
    updated_at: datetime | None


class SimilarItem(CompanyCard):
    score: float


class CompanyDetail(CompanyCard):
    source: str | None
    embedding_status: str


class Meta(_Camel):
    limit: int
    returned: int
    model: str | None
    reason: str | None


class SimilarResponse(_Camel):
    company: CompanyCard
    results: list[SimilarItem]
    meta: Meta


class CompanyNotFound(Exception):
    def __init__(self, registration_number: str) -> None:
        self.registration_number = registration_number


def problem(
    request: Request, status: int, slug: str, title: str, detail: str, **extra: object
) -> JSONResponse:
    body = {"type": PROBLEM_BASE + slug, "title": title, "status": status, "detail": detail,
            "instance": request.url.path, **extra}  # fmt: skip
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _card(row: dict) -> dict:
    """DB row -> card fields. `updatedAt` is shown in Malaysia time; null when the feed never gave a real time."""
    updated = None if row["updated_at_missing"] else row["source_updated_at"].astimezone(MALAYSIA)
    return {k: row[k] for k in ("registration_number", "company_name", "nature_of_business", "state",
                                "industry", "status")} | {"updated_at": updated}  # fmt: skip


def _registration(registration_number: Annotated[str, Path(alias="registrationNumber")]) -> str:
    reg = normalize_registration_number(registration_number)
    if reg is None:
        raise RequestValidationError(
            [{"loc": ("path", "registrationNumber"), "msg": "must not be blank", "type": "value_error"}]
        )
    return reg


def _load_visible(request: Request, reg: str, role: Role) -> dict:
    with request.app.state.pool.connection() as conn:
        company = repository.get_company(conn, reg)
    if company is None or (company["status"] == "struck_off" and role != "analyst"):
        raise CompanyNotFound(reg)
    return company


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    def configure(conn: psycopg.Connection) -> None:
        register_vector(conn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.migrate(settings.database_url)  # creates the vector extension before connections register the type
        pool = ConnectionPool(settings.database_url, min_size=1, max_size=settings.pool_max, timeout=5.0,
                              configure=configure, check=ConnectionPool.check_connection, kwargs={"autocommit": True}, open=False)  # fmt: skip
        pool.open(wait=True)
        app.state.pool = pool
        yield
        pool.close()

    app = FastAPI(title="Company recommendations", version="1", lifespan=lifespan)
    Registration = Annotated[str, Depends(_registration)]
    RoleHeader = Annotated[Role, Header(alias="X-Role")]

    @app.get("/v1/companies/{registrationNumber}/similar", response_model=SimilarResponse)
    def similar(
        request: Request,
        registration_number: Registration,
        role: RoleHeader = "portal",
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        state: str | None = None,
        industry: str | None = None,
    ) -> dict:
        company = _load_visible(request, registration_number, role)
        results: list[dict] = []
        if company["embedding"] is not None:
            with request.app.state.pool.connection() as conn:
                results = repository.similar(conn, company, limit, normalize_state(state), (industry or "").strip() or None,
                                             include_struck_off=role == "analyst")  # fmt: skip
        return {
            "company": _card(company),
            "results": [_card(r) | {"score": round(r["score"], 6)} for r in results],
            "meta": {"limit": limit, "returned": len(results), "model": company["embedding_model"],
                     "reason": None if company["embedding"] is not None else "no_description"},
        }  # fmt: skip

    @app.get("/v1/companies/{registrationNumber}", response_model=CompanyDetail)
    def detail(request: Request, registration_number: Registration, role: RoleHeader = "portal") -> dict:
        company = _load_visible(request, registration_number, role)
        return _card(company) | {"source": company["source"], "embedding_status": company["embedding_status"]}

    @app.get("/", include_in_schema=False)
    def demo_page() -> FileResponse:  # shortcut: static demo UI, no auth; it only calls the endpoints above
        return FileResponse(FilePath(__file__).parent / "static" / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> dict:
        with request.app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.exception_handler(CompanyNotFound)
    def _not_found(request: Request, exc: CompanyNotFound) -> JSONResponse:
        return problem(request, 404, "company-not-found", "Company not found",
                       f"No company with registration number {exc.registration_number}")  # fmt: skip

    @app.exception_handler(RequestValidationError)
    def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()
        ]
        return problem(
            request, 422, "validation-error", "Validation error", "The request is invalid.", errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug = "not-found" if exc.status_code == 404 else f"http-{exc.status_code}"
        return problem(request, exc.status_code, slug, str(exc.detail), str(exc.detail))

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg.InterfaceError)
    def _db_down(request: Request, exc: Exception) -> JSONResponse:
        log.warning("database unavailable: %s", exc)
        return problem(
            request, 503, "service-unavailable", "Service unavailable", "The database is unreachable."
        )

    @app.exception_handler(Exception)
    def _boom(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)  # the traceback stays in the log
        return problem(request, 500, "internal-error", "Internal error", "Something went wrong.")

    return app
