"""FastAPI app: POST /ask, GET /summary, GET /health."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, load_settings
from .data import MissingFxError, load_dataset
from .fx import FxTable
from .llm import Interpreter, OpenAICompatibleInterpreter, StubInterpreter, build_system_prompt, build_tools
from .policy import load_policy
from .schemas import AskRequest, AskResponse
from .service import AssistantService, http_status_for
from .tools import Context

log = logging.getLogger("spend_assistant")


def build_service(settings: Settings, interpreter: Interpreter | None = None) -> AssistantService:
    fx = FxTable.from_ecb_csv(settings.data_dir / "fx" / "ecb_exr_2025.csv")
    ctx = Context(
        data=load_dataset(settings.data_dir, fx),  # raises DataValidationError: the app does not start on bad data
        policy=load_policy(settings.data_dir / "procurement_policy.md"),
        fx=fx,
    )
    if interpreter is None:
        if settings.llm_provider == "stub":
            interpreter = StubInterpreter(ctx.data)
        else:
            interpreter = OpenAICompatibleInterpreter(
                settings, build_system_prompt(ctx.data, ctx.policy), build_tools(ctx.data))
    return AssistantService(ctx, interpreter)


def create_app(settings: Settings | None = None, interpreter: Interpreter | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = build_service(settings, interpreter)
        log.info("Interpreter: %s", app.state.service.interpreter.label)
        yield

    app = FastAPI(title="Finance Spend Assistant", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/health")
    def health():
        svc: AssistantService = app.state.service
        return {"status": "ok", "interpreter": svc.interpreter.label, "versions": svc.versions.model_dump()}

    @app.get("/summary")
    def summary():
        svc: AssistantService = app.state.service
        try:
            return svc.summary()
        except MissingFxError as e:
            return JSONResponse(status_code=503, content={"error": {"code": "fx_rate_missing", "message": str(e)}})

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest):
        svc: AssistantService = app.state.service
        resp = svc.ask(req.question, req.context)
        log.info("ask trace=%s status=%s tool=%s", resp.trace_id, resp.status, resp.interpretation.tool)
        return JSONResponse(status_code=http_status_for(resp), content=resp.model_dump(mode="json"))

    return app


app = create_app()
