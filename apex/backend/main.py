"""
Apex Unified Trading Terminal — Backend
Central FastAPI server mounting all domain routers and WebSocket hub.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import List, Dict, Any
import asyncio
import hmac
import json
import os
import logging
import importlib

from dotenv import load_dotenv

# Load .env from Apex backend only
_apex_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_apex_env):
    load_dotenv(_apex_env)

logger = logging.getLogger("apex")
logging.basicConfig(level=logging.INFO)
# Avoid leaking query-string secrets (e.g., apiKey=...) in verbose client logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Router Imports ──
from routers import alpaca, kalshi, dfs, polymarket, settings, system, notifications, jobs
from core.state import state as global_state
from core import job_store


# ── WebSocket Hub ──
class WebSocketHub:
    """Central WebSocket manager for all domains."""

    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        logger.info(f"WS connected. Total: {len(self.connections)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)
            logger.info(f"WS disconnected. Total: {len(self.connections)}")

    async def broadcast(self, channel: str, msg_type: str, data: Any):
        """Send a message to all connected clients."""
        message = {"channel": channel, "type": msg_type, "data": data}
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.connections:
                self.connections.remove(ws)


hub = WebSocketHub()


# ── App Lifecycle ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Apex Terminal starting up...")
    logger.info(f"   Backend root: {os.path.dirname(os.path.abspath(__file__))}")
    recovered = job_store.fail_stale_jobs(max_age_seconds=3 * 60 * 60)
    if recovered:
        logger.warning("Recovered %d stale jobs on startup", recovered)
    yield
    logger.info("🛑 Apex Terminal shutting down...")


# ── FastAPI App ──
app = FastAPI(
    title="Apex Trading Terminal",
    description="Unified trading API for Alpaca, Kalshi, DFS, and Polymarket",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount Routers ──
app.include_router(alpaca.router, prefix="/api/v1/alpaca", tags=["Alpaca"])
app.include_router(kalshi.router, prefix="/api/v1/kalshi", tags=["Kalshi"])
app.include_router(dfs.router, prefix="/api/v1/dfs", tags=["DFS"])
app.include_router(polymarket.router, prefix="/api/v1/polymarket", tags=["Polymarket"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(system.router, prefix="/api/v1/system", tags=["System"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["Jobs"])

# Expose hub for routers to import
app.state.hub = hub

# Initialize Notification Service
from services import notification_manager
notification_manager.set_hub(hub)


_LIVE_ROUTE_PREFIXES = (
    "/api/v1/alpaca",
    "/api/v1/kalshi",
    "/api/v1/polymarket",
    "/api/v1/dfs",
)
_DOMAIN_ROUTE_PREFIXES = {
    "stocks": ("/api/v1/alpaca",),
    "events": ("/api/v1/kalshi", "/api/v1/polymarket"),
    "sports": ("/api/v1/dfs",),
}
_PAUSE_EXEMPT_PREFIXES = (
    "/api/v1/system",
    "/api/v1/notifications",
    "/api/v1/settings",
    "/api/v1/jobs",
    "/api/v1/health",
)
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WRITE_AUTH_EXEMPT_PREFIXES = (
    "/api/v1/health",
    "/api/v1/health/imports",
)
_WRITE_API_KEY = (os.getenv("APEX_WRITE_API_KEY") or "").strip()


@app.middleware("http")
async def write_auth_guard(request: Request, call_next):
    """
    Optional write-operation auth.
    If APEX_WRITE_API_KEY is configured, non-read API calls must include matching x-api-key.
    """
    if not _WRITE_API_KEY:
        return await call_next(request)
    if request.method.upper() not in _WRITE_METHODS:
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return await call_next(request)
    if any(path.startswith(prefix) for prefix in _WRITE_AUTH_EXEMPT_PREFIXES):
        return await call_next(request)

    provided = (request.headers.get("x-api-key") or "").strip()
    if not hmac.compare_digest(provided, _WRITE_API_KEY):
        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "detail": "Missing or invalid x-api-key for write operation.",
            },
        )
    return await call_next(request)


@app.middleware("http")
async def sleep_mode_guard(request: Request, call_next):
    path = request.url.path
    if global_state.is_paused:
        if any(path.startswith(prefix) for prefix in _PAUSE_EXEMPT_PREFIXES):
            return await call_next(request)
        if any(path.startswith(prefix) for prefix in _LIVE_ROUTE_PREFIXES):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "paused",
                    "error": "sleep_mode",
                    "detail": "System is in sleep mode; live routes are temporarily disabled.",
                },
            )
    else:
        for domain, prefixes in _DOMAIN_ROUTE_PREFIXES.items():
            if global_state.is_domain_paused(domain) and any(path.startswith(prefix) for prefix in prefixes):
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "paused",
                        "error": "domain_sleep_mode",
                        "domain": domain,
                        "detail": f"{domain} domain is offline; live routes are temporarily disabled.",
                    },
                )
    return await call_next(request)


# ── Core Endpoints ──
@app.get("/")
async def root():
    return {"name": "Apex Trading Terminal", "version": "1.0.0"}


@app.get("/api/v1/health")
async def health():
    """Aggregated health check across all services."""
    status = {"apex": "healthy"}

    # Check Alpaca
    try:
        from market_scanner import MarketScanner
        status["alpaca"] = "connected"
    except Exception:
        status["alpaca"] = "unavailable"

    # Check Kalshi
    try:
        from routers.kalshi import _get_api
        _get_api()
        status["kalshi"] = "connected"
    except Exception:
        status["kalshi"] = "unavailable"

    return {"status": status}


@app.get("/api/v1/health/imports")
async def import_health():
    """
    Debug endpoint to verify which concrete modules are resolved at runtime.
    Helps detect cross-project import collisions after deployment.
    """
    modules = {}
    for name in ("api_client", "risk_manager", "config"):
        try:
            mod = importlib.import_module(name)
            modules[name] = getattr(mod, "__file__", "unknown")
        except Exception as e:
            modules[name] = f"error: {e}"
    return {"modules": modules}


# ── Unified WebSocket Endpoint ──
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if global_state.is_paused:
        await ws.close(code=1008, reason="system_paused")
        return
    await hub.connect(ws)
    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await ws.receive_text()
            msg = json.loads(data)

            # Client can subscribe to specific channels
            if msg.get("action") == "ping":
                await ws.send_json({"channel": "system", "type": "pong", "data": {}})

    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        hub.disconnect(ws)
