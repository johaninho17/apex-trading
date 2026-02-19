"""
Settings Router — CRUD endpoints for Apex configuration.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict
from core.config_manager import get_config, update_config, reset_config, DEFAULTS

router = APIRouter()


class ConfigResponse(BaseModel):
    config: Dict[str, Any]


class ConfigUpdateRequest(BaseModel):
    updates: Dict[str, Any]


@router.get("", response_model=ConfigResponse)
async def read_settings():
    """Return full configuration."""
    return {"config": get_config()}


@router.get("/defaults", response_model=ConfigResponse)
async def read_defaults():
    """Return the default configuration (read-only reference)."""
    return {"config": DEFAULTS}


@router.post("", response_model=ConfigResponse)
async def write_settings(body: ConfigUpdateRequest):
    """Merge partial updates into the config and persist."""
    try:
        updated = update_config(body.updates)
        return {"config": updated}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reset", response_model=ConfigResponse)
async def reset_settings():
    """Reset all settings to defaults."""
    return {"config": reset_config()}
