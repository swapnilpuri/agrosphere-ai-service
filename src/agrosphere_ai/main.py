"""Default-disabled M4 HTTP adapter; health only without an owned activation manifest."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
import re
import threading
from uuid import UUID
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from agrosphere_ai.application.ports.m4 import authorize
from agrosphere_ai.application.assistant import compose
from agrosphere_ai.domain.baselines import Scope, forecast
from agrosphere_ai.adapters.codec import load_json, validate_response, wire
from agrosphere_ai.adapters.reference_http import NotFound, Unavailable
from agrosphere_ai.adapters.runtime import configured_runtime
from agrosphere_ai.adapters.store import StaleDataset


class AuthenticationRequired(Exception):
    pass


class AdmissionLimit(Exception):
    pass


def create_app(runtime=None, use_environment=False):
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = configured_runtime() if use_environment else runtime
        worker = thread = None
        if app.state.runtime and os.environ.get("AI_CONSUMER_ENABLED") == "true":
            from agrosphere_ai.adapters.consumer import Consumer
            worker = Consumer(app.state.runtime.store, os.environ["AI_AMQP_URL"], app.state.runtime.guard.value["queue"], app.state.runtime.guard.value["diagnostics"])
            thread = threading.Thread(target=worker.run, daemon=True)
            thread.start()
        try:
            yield
        finally:
            if worker:
                worker.stop.set()
                thread.join(timeout=5)

    app = FastAPI(title="AgroSphere AI Service", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime

    async def failure(request, error):
        status = (401 if isinstance(error, AuthenticationRequired) else 403 if isinstance(error, PermissionError) else
                  404 if isinstance(error, NotFound) else 409 if isinstance(error, StaleDataset) else
                  413 if isinstance(error, AdmissionLimit) else 400 if isinstance(error, ValueError) else 503)
        return JSONResponse({"type": "about:blank", "title": "AI request unavailable" if status == 503 else "AI request denied",
                             "status": status, "detail": "No answer or evidence is inferred from this result."}, status_code=status,
                            media_type="application/problem+json", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def sanitized_failures(request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            # Consume exceptions here so server error logging never serializes a payload/token.
            return await failure(request, error)

    @app.get("/health/live", include_in_schema=False)
    def live():
        return {"status": "UP"}

    def enter(request, estate_id, block_id):
        services = request.app.state.runtime
        if services is None:
            raise Unavailable("Generated M4 is disabled")
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise AuthenticationRequired()
        bearer = auth[7:]
        try:
            principal = services.jwt.verify(bearer)
        except PermissionError as error:
            raise AuthenticationRequired() from error
        if str(UUID(estate_id)) != estate_id or str(UUID(block_id)) != block_id:
            raise ValueError("Canonical scope required")
        scope = Scope(estate_id, block_id)
        authorize(principal, scope)
        services.guard.verify(scope)
        services.references.require_active(scope, bearer)
        return services, scope, bearer

    def query(request, required, optional=()):
        q = request.query_params
        if not set(required) <= set(q) or set(q) - set(required) - set(optional) or any(len(q.getlist(k)) != 1 for k in q):
            raise ValueError("Unknown/repeated/missing query")
        return dict(q)

    def digest(value):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid record identity")
        return value

    def frozen(services, scope, q):
        ds = services.store.dataset(scope, digest(q["datasetId"]), q["mode"])
        as_of = datetime.fromisoformat(q["asOf"].replace("Z", "+00:00"))
        label = datetime.fromisoformat(q["labelAsOf"].replace("Z", "+00:00")) if q.get("labelAsOf") else None
        if ds.mode == "LIVE_FIRST_RECEIPT" and (as_of > datetime.now(timezone.utc) or label and label > datetime.now(timezone.utc)):
            raise ValueError("Live reads cannot use future cutoffs")
        return forecast(ds, as_of, label)

    def respond(schema, value, maximum=1048576):
        value = validate_response(schema, value)
        if len(json.dumps(value).encode()) > maximum:
            raise AdmissionLimit()
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    prefix = "/api/v1/ai/estates/{estate_id}/blocks/{block_id}"

    @app.get(prefix + "/production/baselines")
    def baselines(estate_id: str, block_id: str, request: Request):
        services, scope, _ = enter(request, estate_id, block_id)
        q = query(request, {"datasetId", "mode", "asOf"}, {"labelAsOf"})
        return respond("Forecast", wire(frozen(services, scope, q)))

    @app.get(prefix + "/models/{model_id}")
    def model(estate_id: str, block_id: str, model_id: str, request: Request):
        services, scope, _ = enter(request, estate_id, block_id)
        q = query(request, {"datasetId"})
        return respond("ModelRecord", services.store.record("model", scope, digest(q["datasetId"]), digest(model_id)))

    @app.get(prefix + "/evaluations/{evaluation_id}")
    def evaluation(estate_id: str, block_id: str, evaluation_id: str, request: Request):
        services, scope, _ = enter(request, estate_id, block_id)
        q = query(request, {"datasetId"})
        return respond("Evaluation", services.store.record("evaluation", scope, digest(q["datasetId"]), digest(evaluation_id)))

    @app.post(prefix + "/assistant")
    async def assistant(estate_id: str, block_id: str, request: Request):
        services, scope, bearer = enter(request, estate_id, block_id)
        query(request, set())
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1048576:
                raise AdmissionLimit()
        try:
            q = load_json(raw)
            validate_response("AssistantRequest", q)
        except Exception as error:
            raise ValueError("Closed assistant request required") from error
        value = compose(scope, frozen(services, scope, q), q["intent"], services.references, bearer, q["environmentFrom"], q["environmentTo"])
        return respond("AssistantAnswer", value, 65536)

    return app


app = create_app(use_environment=True)
