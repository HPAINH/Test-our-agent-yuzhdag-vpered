import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import ROOT, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
UI_DIST = ROOT / "ui" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.seed import ensure_seeded

    settings = get_settings()
    if settings.seed_on_start:
        ensure_seeded()
    else:
        from app.db.session import init_db

        init_db()
    sched = None
    if settings.rss_enabled and settings.news_refresh_min > 0:
        from app.worker import start_background

        sched = start_background(settings.news_refresh_min)
    yield
    if sched is not None:
        sched.shutdown(wait=False)


app = FastAPI(
    title="Elbrus News Insights API",
    version="1.0.0",
    description="Персонализированная новостная аналитика для руководителей крупного бизнеса. Прототип команды «Эльбрус».",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(
        status_code=exc.status_code, content={"error": {**detail, "details": detail.get("details")}}
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation", "message": "Некорректный запрос", "details": exc.errors()}},
    )


app.include_router(router)


@app.middleware("http")
async def frame_policy(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        ancestors = get_settings().embed_allowed_origins.strip() or "'self'"
        response.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = (UI_DIST / path).resolve()
        if path and target.is_file() and UI_DIST.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(UI_DIST / "index.html")


def export_openapi(path: Path = ROOT / "docs" / "openapi.json") -> None:
    import json

    path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
