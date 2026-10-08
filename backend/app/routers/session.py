"""対応する mod の利用開始。ログイン・Ubichill 発行元の登録は不要。"""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config import RATE_LIMIT_SESSION, SERVICE_ALLOWED_MODS
from ..rate_limit import RateLimiter, parse_rate
from ..service_auth import client_host, issue_service_token, service_subject

router = APIRouter()
session_limiter = RateLimiter(*parse_rate(RATE_LIMIT_SESSION))


class SessionRequest(BaseModel):
    modId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@router.post("/session")
async def create_session(body: SessionRequest, request: Request, response: Response):
    if body.modId not in SERVICE_ALLOWED_MODS:
        raise HTTPException(status_code=403, detail={"error": "MOD_NOT_ALLOWED"})
    host = client_host(request)
    wait = session_limiter.take(service_subject(host))
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail={"error": "RATE_LIMITED"},
            headers={"Retry-After": str(int(wait) + 1)},
        )
    response.headers["Cache-Control"] = "no-store"
    return issue_service_token(host, body.modId)
