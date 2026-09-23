"""Loopback-only owner session endpoints; never return a bootstrap secret."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from ...local_owner_auth import COOKIE_NAME, LocalOwnerPrincipal
from ..errors import ApiError

router = APIRouter(prefix="/local-owner", tags=["local-owner"])


class BootstrapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


def _loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_local_request(request: Request, *, mutation: bool = False,
                          intent: str | None = None) -> None:
    """Reject remote/proxied surfaces and cross-origin cookie mutations."""
    peer = request.client.host if request.client else None
    if (not _loopback(peer) or not _loopback(request.url.hostname)
            or any(key in request.headers for key in (
                "forwarded", "x-forwarded-for", "x-real-ip"))):
        raise ApiError(403, "local_only", "owner authentication requires direct loopback")
    if not mutation:
        return
    origin = request.headers.get("origin")
    if not origin:
        raise ApiError(403, "owner_origin_required", "owner action requires browser origin")
    parsed = urlsplit(origin)
    allowed = set(request.app.state.settings.cors_origin_list)
    allowed.add(f"{request.url.scheme}://{request.url.netloc}")
    if (parsed.scheme not in {"http", "https"}
            or not _loopback(parsed.hostname) or origin not in allowed):
        raise ApiError(403, "owner_origin_denied", "untrusted owner request origin")
    if intent and request.headers.get("x-rintel-owner-intent") != intent:
        raise ApiError(403, "owner_intent_required", "explicit owner action required")


def owner_principal(request: Request) -> LocalOwnerPrincipal:
    require_local_request(request)
    principal = request.app.state.local_owner_auth.resolve(
        request.cookies.get(COOKIE_NAME))
    if principal is None:
        raise ApiError(401, "owner_session_required", "authenticated local owner required")
    return principal


@router.post("/bootstrap")
def bootstrap(body: BootstrapBody, request: Request, response: Response) -> dict:
    require_local_request(request, mutation=True)
    session = request.app.state.local_owner_auth.authenticate(body.token)
    if session is None:
        raise ApiError(401, "invalid_bootstrap", "bootstrap token invalid or expired")
    response.set_cookie(
        COOKIE_NAME, session.token, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", path="/api/v1",
        max_age=request.app.state.local_owner_auth.session_ttl)
    return {"authenticated": True, "principal": session.principal.public(),
            "policy_version": "local-owner-approval/1"}


@router.get("/session")
def session_status(request: Request) -> dict:
    require_local_request(request)
    principal = request.app.state.local_owner_auth.resolve(
        request.cookies.get(COOKIE_NAME))
    return {"authenticated": principal is not None,
            "principal": principal.public() if principal else None,
            "policy_version": "local-owner-approval/1",
            "deployment": "LOCAL_SINGLE_OWNER"}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    require_local_request(request, mutation=True, intent="logout")
    revoked = request.app.state.local_owner_auth.revoke(
        request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/api/v1")
    return {"authenticated": False, "revoked": revoked}
