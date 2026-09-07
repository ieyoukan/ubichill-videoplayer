"""Removed public URL proxy. Kept as an unmounted compatibility tombstone."""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/proxy")
async def proxy_url():
    raise HTTPException(status_code=410, detail="Use the opaque /stream/{id}/resource/{token} gateway")
