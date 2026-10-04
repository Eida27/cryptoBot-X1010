import hashlib
import secrets
from collections import defaultdict, deque
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request

from crypto_bot.storage.repository import Repository


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, repo: Repository, password_hash: str, clock: Any) -> None:
        self.repo, self.password_hash, self.clock = repo, password_hash, clock
        self.failures: dict[str, deque[int]] = defaultdict(deque)

    def check_origin(self, request: Request) -> None:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin != expected:
            raise HTTPException(403, "Same-origin POST required")

    def verify_password(self, password: str, source: str) -> bool:
        now = self.clock.now_ms()
        for key in ("account", source):
            attempts = self.failures[key]
            while attempts and now - attempts[0] >= 900000:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(429, "Login temporarily throttled")
        try:
            accepted = bool(self.password_hash) and PasswordHasher().verify(self.password_hash, password)
        except (VerificationError, InvalidHashError):
            accepted = False
        if not accepted:
            self.failures["account"].append(now)
            self.failures[source].append(now)
        return accepted

    def create_session(self) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = self.clock.now_ms()
        self.repo.db.connection.execute("DELETE FROM web_sessions WHERE revoked=1 OR last_seen_ms<?", (now - 1800000,))
        self.repo.db.connection.execute("INSERT INTO web_sessions VALUES(?,?,?,?,0)", (token_hash(token), token_hash(csrf), now, now))
        return token, csrf

    def session(self, request: Request, csrf: str | None = None) -> Any:
        token = request.cookies.get("cbot_session", "")
        row = self.repo.db.connection.execute("SELECT * FROM web_sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
        now = self.clock.now_ms()
        if row is None or row["revoked"] or not 0 <= now - row["last_seen_ms"] < 1800000:
            raise HTTPException(401, "Session expired; sign in again")
        if csrf is not None and not secrets.compare_digest(row["csrf_hash"], token_hash(csrf)):
            raise HTTPException(403, "Invalid CSRF token")
        self.repo.db.connection.execute("UPDATE web_sessions SET last_seen_ms=? WHERE token_hash=?", (now, row["token_hash"]))
        return row
