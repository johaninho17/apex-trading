from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from core import job_store

router = APIRouter()


class JobResponse(BaseModel):
    id: str
    domain: str
    kind: str
    status: str
    progress: int = 0
    total: int = 0
    message: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: List[JobResponse] = Field(default_factory=list)
    count: int = 0


@router.get("", response_model=JobListResponse)
async def list_all_jobs(
    domain: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = job_store.list_jobs(domain=domain, status=status, limit=limit)
    return {"jobs": rows, "count": len(rows)}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    row = job_store.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row
