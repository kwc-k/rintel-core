"""Single-instance, loopback-only owner authentication authority.

The bootstrap secret is delivered to an operator terminal, never a public API.
This deliberately does not claim protection from a process with the same OS
user's debugger, terminal, or browser-profile access.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable


POLICY_VERSION = "local-owner-approval/1"
COOKIE_NAME = "rintel_owner_session"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LocalOwnerPrincipal:
    principal_id: str
    principal_type: str
    rintel_instance_id: str
    auth_method: str
    auth_session_id: str
    authenticated_at: int
    last_validated_at: int
    expires_at: int
    authorization: str
    host_binding: str

    def public(self) -> dict:
        return vars(self).copy()


@dataclass(frozen=True)
class AuthenticatedSession:
    token: str
    principal: LocalOwnerPrincipal


class LocalOwnerAuthority:
    """In-memory sessions die with the daemon; no client may select identity."""

    def __init__(self, *, clock: Callable[[], float] | None = None,
                 bootstrap_ttl: int = 600, session_ttl: int = 3600):
        if bootstrap_ttl <= 0 or session_ttl <= 0:
            raise ValueError("authentication lifetimes must be positive")
        self.clock = clock or time.time
        self.bootstrap_ttl = bootstrap_ttl
        self.session_ttl = session_ttl
        self.instance_id = f"rintel-instance-{uuid.uuid4().hex}"
        self.principal_id = f"local-owner-{uuid.uuid4().hex}"
        self._lock = threading.RLock()
        self._bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_hash = _hash(self._bootstrap_token)
        self._bootstrap_expires = self.clock() + bootstrap_ttl
        self._sessions: dict[str, LocalOwnerPrincipal] = {}

    def _take_operator_bootstrap_token(self) -> str | None:
        """Private in-process operator handoff; never route this to REST/MCP."""
        with self._lock:
            token = self._bootstrap_token
            self._bootstrap_token = None
            return token if self.clock() < self._bootstrap_expires else None

    def deliver_bootstrap_to_tty(self) -> bool:
        """Display once on the daemon's controlling TTY, not stdout/log files."""
        try:
            with open("/dev/tty", "w", encoding="utf-8") as terminal:
                token = self._take_operator_bootstrap_token()
                if token is None:
                    return False
                terminal.write("Rintel Local Owner one-time bootstrap token: "
                               f"{token}\nExpires in {self.bootstrap_ttl} seconds.\n")
                terminal.flush()
            return True
        except OSError:
            # A detached daemon has no trusted operator delivery channel.
            return False

    def authenticate(self, token: str) -> AuthenticatedSession | None:
        if not isinstance(token, str) or not token:
            return None
        with self._lock:
            if (not self._bootstrap_hash or self.clock() >= self._bootstrap_expires
                    or not hmac.compare_digest(_hash(token), self._bootstrap_hash)):
                return None
            self._bootstrap_hash = None
            self._bootstrap_token = None
            now = int(self.clock())
            principal = LocalOwnerPrincipal(
                principal_id=self.principal_id, principal_type="LOCAL_OWNER",
                rintel_instance_id=self.instance_id, auth_method="BOOTSTRAP_TOKEN",
                auth_session_id=f"owner-session-{uuid.uuid4().hex}",
                authenticated_at=now, last_validated_at=now,
                expires_at=now + self.session_ttl,
                authorization="OWNER", host_binding="LOOPBACK")
            session_token = secrets.token_urlsafe(32)
            self._sessions[_hash(session_token)] = principal
            return AuthenticatedSession(session_token, principal)

    def resolve(self, session_token: str | None) -> LocalOwnerPrincipal | None:
        if not session_token:
            return None
        with self._lock:
            key = _hash(session_token)
            principal = self._sessions.get(key)
            if not principal:
                return None
            if (principal.rintel_instance_id != self.instance_id
                    or self.clock() >= principal.expires_at):
                self._sessions.pop(key, None)
                return None
            principal = replace(principal, last_validated_at=int(self.clock()))
            self._sessions[key] = principal
            return principal

    def revoke(self, session_token: str | None) -> bool:
        if not session_token:
            return False
        with self._lock:
            return self._sessions.pop(_hash(session_token), None) is not None

    def approval_authorization(self, session_token: str | None) -> dict | None:
        principal = self.resolve(session_token)
        if principal is None or principal.authorization != "OWNER":
            return None
        return {
            "subject": principal.principal_id,
            "principal_id": principal.principal_id,
            "principal_type": principal.principal_type,
            "auth_session_id": principal.auth_session_id,
            "rintel_instance_id": principal.rintel_instance_id,
            "auth_method": principal.auth_method,
            "permission": "approve_design",
            "authorization_id": f"{POLICY_VERSION}:{principal.principal_id}",
            "policy_version": POLICY_VERSION,
        }
