from fastapi import APIRouter
from pydantic import BaseModel
from core.state import state
from services.notification_manager import send_toast

router = APIRouter()

class SleepRequest(BaseModel):
    enabled: bool


class DomainSleepRequest(BaseModel):
    domain: str
    enabled: bool


@router.post("/sleep")
async def toggle_sleep(request: SleepRequest):
    """Toggle global offline mode (pauses background workers)."""
    state.set_paused(request.enabled)
    await send_toast(
        title="System Offline" if request.enabled else "System Live",
        message="Live calls are paused." if request.enabled else "Live services resumed.",
        type="info",
    )
    status = "offline" if request.enabled else "live"
    return {"message": f"System is now {status}", "paused": request.enabled}


@router.post("/domain")
async def toggle_domain_sleep(request: DomainSleepRequest):
    domain = (request.domain or "").strip().lower()
    if domain not in {"stocks", "events", "sports"}:
        return {"ok": False, "error": "invalid_domain", "allowed": ["stocks", "events", "sports"]}
    state.set_domain_paused(domain, request.enabled)
    await send_toast(
        title=f"{domain.title()} {'Offline' if request.enabled else 'Live'}",
        message=f"{domain.title()} live calls are {'paused' if request.enabled else 'resumed'}.",
        type="info",
    )
    return {"ok": True, "domain": domain, "paused": request.enabled, "domain_paused": state.domain_paused}


@router.get("/status")
async def get_system_status():
    return {"paused": state.paused, "domain_paused": state.domain_paused}
