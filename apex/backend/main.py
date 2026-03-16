"""
Apex Unified Trading Terminal — Backend
Central FastAPI server mounting all domain routers and WebSocket hub.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from urllib.parse import urlparse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
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
from core.state import state as global_state
from core import job_store
from core import background_services

_DEFAULT_ROUTE_PACKS = {
    "crypto": True,
    "crypto_compat": True,
    "system": True,
    "settings": True,
    "notifications": True,
    "jobs": True,
    "kalshi": False,
    "dfs": False,
    "polymarket": False,
    "legacy_alpaca": False,
    "stocks_ml": False,
}
_ROUTE_PACKS = {
    "crypto": ("routers.crypto", "router", "/api/v1/crypto", ["Crypto"]),
    "crypto_compat": ("routers.crypto", "compat_router", "/api/v1/alpaca", ["Alpaca Crypto Compatibility"]),
    "system": ("routers.system", "router", "/api/v1/system", ["System"]),
    "settings": ("routers.settings", "router", "/api/v1/settings", ["Settings"]),
    "notifications": ("routers.notifications", "router", "/api/v1/notifications", ["Notifications"]),
    "jobs": ("routers.jobs", "router", "/api/v1/jobs", ["Jobs"]),
    "kalshi": ("routers.kalshi", "router", "/api/v1/kalshi", ["Kalshi"]),
    "dfs": ("routers.dfs", "router", "/api/v1/dfs", ["DFS"]),
    "polymarket": ("routers.polymarket", "router", "/api/v1/polymarket", ["Polymarket"]),
    "legacy_alpaca": ("routers.alpaca", "router", "/api/v1/alpaca", ["Alpaca Legacy"]),
    "stocks_ml": ("routers.stocks_ml", "router", "/api/v1", ["Stocks ML"]),
}


def _parse_route_pack_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _route_pack_flags() -> Dict[str, bool]:
    from core.config_manager import get_config

    flags = dict(_DEFAULT_ROUTE_PACKS)
    raw_cfg = get_config().get("app", {}).get("enabled_route_packs", {})
    if isinstance(raw_cfg, dict):
        for key, value in raw_cfg.items():
            key_norm = str(key or "").strip().lower()
            if key_norm in flags:
                flags[key_norm] = bool(value)
    for key in _parse_route_pack_env("APEX_ENABLE_ROUTE_PACKS"):
        if key in flags:
            flags[key] = True
    for key in _parse_route_pack_env("APEX_DISABLE_ROUTE_PACKS"):
        if key in flags:
            flags[key] = False
    return flags


def _mount_enabled_route_packs(app: FastAPI) -> Dict[str, bool]:
    flags = _route_pack_flags()
    mounted: Dict[str, bool] = {}
    for pack_name, enabled in flags.items():
        if not enabled:
            mounted[pack_name] = False
            continue
        module_name, attr_name, prefix, tags = _ROUTE_PACKS[pack_name]
        module = importlib.import_module(module_name)
        app.include_router(getattr(module, attr_name), prefix=prefix, tags=tags)
        mounted[pack_name] = True
    return mounted


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
    logger.info("Apex Terminal starting up...")
    logger.info(f"   Backend root: {os.path.dirname(os.path.abspath(__file__))}")
    recovered = job_store.fail_stale_jobs(max_age_seconds=3 * 60 * 60)
    if recovered:
        logger.warning("Recovered %d stale jobs on startup", recovered)

    background_services.bind_current_loop()
    await background_services.ensure_boot_services()
    from services.crypto.terminal_data import schedule_shell_warmup

    schedule_shell_warmup()

    yield

    await background_services.shutdown_all_services()
    logger.info("Apex Terminal shutting down...")
# ── Rate Limiting ──
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Allow a generous default limit for normal usage but prevent endpoint spam bot crashes
limiter = Limiter(key_func=get_remote_address, default_limits=["250/minute"])

# ── FastAPI App ──
app = FastAPI(
    title="Apex Trading Terminal",
    description="Crypto-first trading API with optional secondary route packs",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
_FRONTEND_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|172\.\d+\.\d+\.\d+|[a-zA-Z0-9-]+\.local)(:\d+)?$"
_frontend_origins_env = [o.strip() for o in (os.getenv("APEX_FRONTEND_ORIGINS") or "").split(",") if o.strip()]
_FRONTEND_ALLOWED_ORIGINS = _frontend_origins_env or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_FRONTEND_ALLOWED_ORIGINS,
    allow_origin_regex=_FRONTEND_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount Routers ──
app.state.hub = hub
app.state.route_packs = _mount_enabled_route_packs(app)

# Initialize Notification Service
from services import notification_manager
notification_manager.set_hub(hub)


_mounted_packs = app.state.route_packs
_LIVE_ROUTE_PREFIXES = tuple(
    prefix
    for pack_name, prefix in (
        ("crypto", "/api/v1/crypto"),
        ("crypto_compat", "/api/v1/alpaca/crypto"),
        ("legacy_alpaca", "/api/v1/alpaca"),
        ("kalshi", "/api/v1/kalshi"),
        ("polymarket", "/api/v1/polymarket"),
        ("dfs", "/api/v1/dfs"),
    )
    if _mounted_packs.get(pack_name, False)
)
_DOMAIN_ROUTE_PREFIXES = {
    "stocks": tuple(
        prefix
        for prefix in ("/api/v1/crypto", "/api/v1/alpaca/crypto", "/api/v1/alpaca")
        if prefix in _LIVE_ROUTE_PREFIXES
    ),
    "events": tuple(
        prefix for prefix in ("/api/v1/kalshi", "/api/v1/polymarket") if prefix in _LIVE_ROUTE_PREFIXES
    ),
    "sports": tuple(prefix for prefix in ("/api/v1/dfs",) if prefix in _LIVE_ROUTE_PREFIXES),
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
            try:
                return await call_next(request)
            except asyncio.CancelledError:
                logger.info("Request cancelled during sleep-guard bypass: %s %s", request.method, path)
                return JSONResponse(status_code=499, content={"status": "cancelled"})
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
    try:
        return await call_next(request)
    except asyncio.CancelledError:
        logger.info("Request cancelled: %s %s", request.method, path)
        return JSONResponse(status_code=499, content={"status": "cancelled"})


# ── Core Endpoints ──
@app.get("/")
async def root():
    return {"name": "Apex Trading Terminal", "version": "1.0.0"}


@app.get("/api/v1/health")
async def health():
    """Aggregated health check for the active crypto-first profile."""
    from services.crypto.terminal_data import get_crypto_health

    crypto_state = await get_crypto_health()
    status = {
        "apex": "healthy",
        "crypto": crypto_state.get("status", "unknown"),
        "provider": (crypto_state.get("provider_state") or {}).get("status", "unknown"),
    }
    route_packs = dict(app.state.route_packs or {})
    for pack_name in ("kalshi", "dfs", "polymarket", "legacy_alpaca", "stocks_ml"):
        status[pack_name] = "enabled" if route_packs.get(pack_name, False) else "disabled"

    return {
        "status": status,
        "route_packs": route_packs,
        "services": crypto_state.get("services", {}),
        "shell_cache": crypto_state.get("shell_cache", {}),
    }


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
    # Security: Verify the WebSocket connection originates from a trusted local frontend origin.
    origin = (ws.headers.get("origin") or "").strip()
    parsed_origin = urlparse(origin) if origin else None
    origin_ok = False
    
    if origin:
        if origin in _FRONTEND_ALLOWED_ORIGINS:
            origin_ok = True
        elif parsed_origin and parsed_origin.scheme in {"http", "https"}:
            import re
            if re.match(_FRONTEND_ORIGIN_REGEX, origin):
                origin_ok = True
            elif parsed_origin.hostname in {"localhost", "127.0.0.1"}:
                origin_ok = True
                
    if not origin_ok:
        logger.warning(f"Blocked unauthorized WebSocket connection from origin: {origin}")
        await ws.close(code=1008, reason="invalid_origin")
        return

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
