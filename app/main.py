from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.security import SessionManager
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.dependencies import build_llm_provider
from app.exceptions import AuthorizationError, DomainError, NotFoundError
from app.llm.service import LLMService
from app.reminders.worker import reminder_loop
from app.services.reminder import ReminderService
from app.services.assignment import AssignmentService
from app.services.notification import NotificationService
from app.telegram.client import TelegramClient
from app.telegram.service import TelegramService
from app.web.router import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    worker = asyncio.create_task(reminder_loop(app))
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        app.state.telegram_client.close()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = SessionLocal
    provider = build_llm_provider(settings)
    telegram_client = TelegramClient(settings)
    app.state.sessions = SessionManager(settings)
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.state.llm_service = LLMService(provider)
    app.state.telegram_client = telegram_client
    app.state.notification_service = NotificationService(telegram_client)
    app.state.assignment_service = AssignmentService(app.state.notification_service)
    app.state.telegram_service = TelegramService(app.state.llm_service, telegram_client)
    app.state.reminder_service = ReminderService(provider, telegram_client, settings)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(web_router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info(
            "request id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.exception_handler(AuthorizationError)
    async def authorization_error(_request: Request, _exc: AuthorizationError):
        return JSONResponse(
            status_code=403, content={"detail": "Resource is outside your authorized scope"}
        )

    @app.exception_handler(NotFoundError)
    async def not_found_error(_request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        with app.state.session_factory() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {
            "status": "ok",
            "llm_mode": settings.llm_mode,
            "telegram_mode": settings.telegram_mode,
        }

    @app.post("/telegram/webhook/{secret}")
    async def telegram_webhook(secret: str, request: Request):
        if not __import__("secrets").compare_digest(secret, settings.telegram_webhook_secret):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        try:
            update = await request.json()
        except Exception:
            logger.warning("telegram_webhook_malformed_json")
            return {"ok": True, "result": "ignored_malformed_json"}
        if not isinstance(update, dict):
            return {"ok": True, "result": "ignored_malformed_update"}
        with app.state.session_factory.begin() as db:
            return app.state.telegram_service.process(db, update)

    return app


app = create_app()
