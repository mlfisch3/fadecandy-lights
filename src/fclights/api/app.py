"""The control API.

REST for commands, a WebSocket for state pushes.  See ``docs/api.md`` for the
normative contract; this module is its implementation.

Everything runs on one asyncio loop alongside the render task, so there are no
locks: a request mutates the state store, tells the engine, and broadcasts, all
without another coroutine observing a half-applied change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fclights import __version__, effects
from fclights.api.hub import Broadcaster
from fclights.api.models import (
    BrightnessRequest,
    EffectRequest,
    ParamsRequest,
    PowerRequest,
    SceneCreateRequest,
    SceneUpdateRequest,
)
from fclights.config import Config
from fclights.engine import Engine
from fclights.layout import Layout
from fclights.state import State, StateError, StateStore

log = logging.getLogger(__name__)

# State is mirrored to disk this long after the last change, so dragging a
# brightness slider writes once at the end rather than sixty times a second.
PERSIST_DEBOUNCE_SECONDS = 2.0

# How often the WebSocket pushes telemetry (frame rate, power draw) to clients
# that asked for it. State changes are pushed immediately regardless.
TELEMETRY_INTERVAL_SECONDS = 2.0


class Controller:
    """Ties the state store, the engine and the broadcaster together.

    Every mutating endpoint funnels through :meth:`commit`, which is the single
    place that pushes new state to the engine, broadcasts it, and schedules a
    save.  Doing it once means an endpoint cannot forget one of the three.
    """

    def __init__(
        self,
        store: StateStore,
        engine: Engine,
        layout: Layout,
        config: Config,
    ) -> None:
        self.store = store
        self.engine = engine
        self.layout = layout
        self.config = config
        self.broadcaster = Broadcaster()
        self.started_at = time.time()
        self._persist_task: asyncio.Task[None] | None = None
        self._telemetry_task: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        self.engine.apply_state(self.store.state)
        self.engine.start()
        self._telemetry_task = asyncio.create_task(
            self._telemetry_loop(), name="fclights-telemetry"
        )

    async def shutdown(self) -> None:
        for task in (self._persist_task, self._telemetry_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._persist_task = None
        self._telemetry_task = None
        await self.engine.stop()
        await self.broadcaster.close()
        # Last chance to get the current look onto disk before we exit.
        self.store.save_if_dirty()

    # -- the one mutation path -----------------------------------------

    async def commit(self, state: State) -> State:
        self.engine.apply_state(state)
        await self.broadcaster.broadcast({"type": "state", "state": state.to_dict()})
        self._schedule_persist()
        return state

    def _schedule_persist(self) -> None:
        if self._persist_task is not None and not self._persist_task.done():
            return
        self._persist_task = asyncio.create_task(self._persist_after_debounce())

    async def _persist_after_debounce(self) -> None:
        try:
            await asyncio.sleep(PERSIST_DEBOUNCE_SECONDS)
            self.store.save_if_dirty()
        except asyncio.CancelledError:
            raise

    async def _telemetry_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TELEMETRY_INTERVAL_SECONDS)
                if self.broadcaster.client_count:
                    await self.broadcaster.broadcast(
                        {"type": "telemetry", "status": self.engine.status()}
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telemetry broadcast failed; continuing")

    # -- shared response shapes ----------------------------------------

    def state_payload(self) -> dict[str, Any]:
        return {"type": "state", "state": self.store.state.to_dict()}

    def info_payload(self) -> dict[str, Any]:
        return {
            "service": "fclights",
            "version": __version__,
            "simulated": self.config.simulate,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "layout": self.layout.to_dict(),
            "status": self.engine.status(),
        }


def _controller(request: Request) -> Controller:
    return request.app.state.controller


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", summary="Liveness probe")
    async def health(request: Request) -> dict[str, Any]:
        controller = _controller(request)
        status = controller.engine.status()
        return {
            "ok": True,
            "version": __version__,
            "simulated": controller.config.simulate,
            "opc_connected": status["connected"],
        }

    @router.get("/info", summary="Service, layout and engine status")
    async def info(request: Request) -> dict[str, Any]:
        return _controller(request).info_payload()

    @router.get("/status", summary="Engine, power and connection telemetry")
    async def status(request: Request) -> dict[str, Any]:
        return _controller(request).engine.status()

    @router.get("/layout", summary="Physical layout of the installation")
    async def get_layout(request: Request) -> dict[str, Any]:
        return _controller(request).layout.to_dict()

    @router.get("/state", summary="Everything a client needs to render its UI")
    async def get_state(request: Request) -> dict[str, Any]:
        return _controller(request).state_payload()

    @router.get("/effects", summary="Available effects and their parameter schemas")
    async def get_effects() -> dict[str, Any]:
        return {"effects": effects.schemas()}

    @router.put("/power", summary="Master on/off")
    async def set_power(request: Request, body: PowerRequest) -> dict[str, Any]:
        controller = _controller(request)
        state = controller.store.set_power(body.on)
        await controller.commit(state)
        return controller.state_payload()

    @router.put("/brightness", summary="Global master brightness")
    async def set_brightness(request: Request, body: BrightnessRequest) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state = controller.store.set_brightness(body.brightness)
        except StateError as exc:
            raise _bad_request(exc) from exc
        await controller.commit(state)
        return controller.state_payload()

    @router.put("/effect", summary="Select an effect and set its parameters")
    async def set_effect(request: Request, body: EffectRequest) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state = controller.store.set_effect(body.effect, body.params)
        except effects.UnknownEffectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (effects.ParamError, StateError) as exc:
            raise _bad_request(exc) from exc
        await controller.commit(state)
        return controller.state_payload()

    @router.patch("/effect/params", summary="Update some parameters of the current effect")
    async def patch_params(request: Request, body: ParamsRequest) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state = controller.store.update_params(body.params)
        except (effects.ParamError, effects.UnknownEffectError, StateError) as exc:
            raise _bad_request(exc) from exc
        await controller.commit(state)
        return controller.state_payload()

    @router.get("/scenes", summary="List saved scenes")
    async def list_scenes(request: Request) -> dict[str, Any]:
        state = _controller(request).store.state
        return {
            "scenes": [s.to_dict() for s in state.scenes],
            "active_scene": state.active_scene,
        }

    @router.post("/scenes", status_code=201, summary="Save the live look as a new scene")
    async def create_scene(request: Request, body: SceneCreateRequest) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state, scene = controller.store.save_scene(body.name)
        except StateError as exc:
            raise _bad_request(exc) from exc
        await controller.commit(state)
        return {"scene": scene.to_dict(), "state": state.to_dict()}

    @router.get("/scenes/{scene_id}", summary="Read one scene")
    async def get_scene(request: Request, scene_id: str) -> dict[str, Any]:
        try:
            scene = _controller(request).store.state.scene(scene_id)
        except StateError as exc:
            raise _not_found(exc) from exc
        return {"scene": scene.to_dict()}

    @router.put("/scenes/{scene_id}", summary="Rename a scene, or recapture the live look into it")
    async def update_scene(
        request: Request, scene_id: str, body: SceneUpdateRequest
    ) -> dict[str, Any]:
        controller = _controller(request)
        try:
            existing = controller.store.state.scene(scene_id)
        except StateError as exc:
            raise _not_found(exc) from exc

        if not body.capture and body.name is None:
            raise HTTPException(
                status_code=400, detail="supply a new name, capture=true, or both"
            )
        if not body.capture:
            # Renaming must not silently redefine what the scene shows, so put
            # the stored look back before recapturing under the new name.
            controller.store.recall_scene(scene_id)

        try:
            state, scene = controller.store.save_scene(body.name or existing.name, scene_id)
        except StateError as exc:
            raise _bad_request(exc) from exc
        await controller.commit(state)
        return {"scene": scene.to_dict(), "state": state.to_dict()}

    @router.delete("/scenes/{scene_id}", summary="Delete a scene")
    async def delete_scene(request: Request, scene_id: str) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state = controller.store.delete_scene(scene_id)
        except StateError as exc:
            raise _not_found(exc) from exc
        await controller.commit(state)
        return controller.state_payload()

    @router.post("/scenes/{scene_id}/recall", summary="Make a scene the live look")
    async def recall_scene(request: Request, scene_id: str) -> dict[str, Any]:
        controller = _controller(request)
        try:
            state = controller.store.recall_scene(scene_id)
        except StateError as exc:
            raise _not_found(exc) from exc
        await controller.commit(state)
        return controller.state_payload()

    return router


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def create_app(controller: Controller) -> FastAPI:
    """Build the ASGI app around an already-constructed controller."""
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await controller.start()
        try:
            yield
        finally:
            await controller.shutdown()

    app = FastAPI(
        title="fclights control API",
        version=__version__,
        description=(
            "Control API for a Fadecandy-driven WS2812B installation. "
            "REST for commands, /api/ws for live state pushes."
        ),
        lifespan=lifespan,
    )
    app.state.controller = controller

    if controller.config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(controller.config.server.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(build_router())

    # One error shape everywhere, so the Android client has a single parser.
    # Registering against Starlette's exception rather than FastAPI's is what
    # catches unmatched routes and method mismatches too; FastAPI's own subclass
    # only covers the errors our handlers raise.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(
            exc.status_code, str(exc.detail)
        ))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Flatten pydantic's structured errors into the same {error, detail}
        # shape, naming the offending fields so a client bug is diagnosable
        # from the message alone.
        parts = []
        for error in exc.errors():
            location = ".".join(str(p) for p in error.get("loc", ()) if p != "body")
            parts.append(f"{location}: {error.get('msg', 'invalid')}" if location
                         else str(error.get("msg", "invalid")))
        return JSONResponse(
            status_code=422, content=_error_body(422, "; ".join(parts) or "invalid request body")
        )

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        ctrl: Controller = websocket.app.state.controller
        await websocket.accept()
        await ctrl.broadcaster.add(websocket)
        try:
            # Send the full picture on connect so a phone that just woke up is
            # immediately in sync without a separate REST round trip.
            await websocket.send_json(
                {
                    "type": "hello",
                    "version": __version__,
                    "state": ctrl.store.state.to_dict(),
                    "layout": ctrl.layout.to_dict(),
                    "effects": effects.schemas(),
                    "status": ctrl.engine.status(),
                }
            )
            while True:
                # The socket is push-only. Reading keeps the connection alive and
                # lets us notice a disconnect promptly; a client may send "ping".
                message = await websocket.receive_text()
                if message.strip().lower() in {"ping", '"ping"'}:
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.debug("websocket closed unexpectedly", exc_info=True)
        finally:
            await ctrl.broadcaster.remove(websocket)

    return app


def _error_body(status_code: int, detail: str) -> dict[str, str]:
    return {"error": _ERROR_NAMES.get(status_code, "error"), "detail": detail}


_ERROR_NAMES = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    422: "unprocessable_entity",
    500: "internal_error",
}
