"""FastAPI factory + lifecycle + global exception handlers (SPEC-P1 §5/§7).

`uvicorn rintel.server.app:app` (SPEC-P1 §28).  All responses use the
unified error envelope; evidence plane is read-only (no write routers).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pathlib import Path

from ..local_owner_auth import LocalOwnerAuthority
from ..git_workspace import GitWorkspaceManager
from .api import (build_recovery, data_interface, design, design_lifecycle, evidence, execution_authority, flow_infer, flows, git_workspace, graph, jobs_api, repos,
                  local_owner, runtime_api, search, semantic_flow, source, stats, structural, sync_api,
                  topology_view, ui_reality,
                  tree, workspaces)
from .deps import build_store
from .errors import ApiError
from .execution_config import load_execution_registry
from .jobs import JobManager, PgJobsPersister, StoreJobsPersister
from .settings import get_settings

log = logging.getLogger("rintel.server")

APP_VERSION = "0.1.0"


class _SpaFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/") or "." in Path(path).name:
                raise
            return await super().get_response("index.html", scope)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="rintel — Repository Intelligence Workbench API",
        version=APP_VERSION,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings
    app.state.store_factory = build_store
    app.state.local_owner_auth = LocalOwnerAuthority()
    app.state.execution_authority = load_execution_registry(settings)[1]
    app.state.git_workspace_manager = GitWorkspaceManager(
        settings.git_workspace_state_path,
        execution_authority=app.state.execution_authority)
    persister = (PgJobsPersister(settings.database_url, settings.pg_schema)
                 if settings.database_url else StoreJobsPersister(
                     lambda: app.state.store_factory()))
    app.state.jobs = JobManager(persister.persist, load=persister.get)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    _install_handlers(app)
    for router in (repos.router, tree.router, structural.router, search.router,
                   source.router, graph.router, evidence.router, stats.router,
                   jobs_api.router, workspaces.router, design.router,
                   design_lifecycle.router, local_owner.router, git_workspace.router,
                   build_recovery.router,
                   execution_authority.router,
                   flows.router, topology_view.router, flow_infer.router, data_interface.router,
                   semantic_flow.router, sync_api.router, runtime_api.router,
                   ui_reality.router):
        app.include_router(router, prefix="/api/v1")
    web_dist = Path(settings.web_dist_path)
    if (web_dist / "index.html").is_file():
        app.mount("/", _SpaFiles(directory=web_dist, html=True), name="web")
    return app


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.envelope())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error",
                               "message": str(exc.detail), "details": {}}})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_error", "message": "request validation failed",
            "details": {"errors": exc.errors()}}})

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "internal server error",
            "details": {}}})


app = create_app()
