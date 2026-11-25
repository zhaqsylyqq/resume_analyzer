"""Passwords (Argon2id), the session fingerprint and validation of user input (A1, S2, SEC-01)."""
from __future__ import annotations

import hashlib
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..services import storage

USERNAME_RE = re.compile(r"^[\w.\-]{3,64}$", re.UNICODE)
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_EMAIL = 254

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
# A valid hash of a random password: verifying against it when the user does not exist
# costs the same time as a real check, so response time does not reveal usernames.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _fingerprint(password_hash: str, totp_secret: str | None = None) -> str:
    """Stored in the session: changing the password or switching two-factor sign-in on or off
    signs out every other session of that user (SEC-01)."""
    return hashlib.sha256(f"{password_hash}|{totp_secret or ''}".encode()).hexdigest()[:16]


def fingerprint(user) -> str:
    return _fingerprint(user["password_hash"], storage.totp_secret(user))


def validate_username(username: str) -> str | None:
    return None if USERNAME_RE.match(username or "") else "auth.error.username"


def validate_email(email: str, required: bool) -> str | None:
    if not email:
        return "auth.error.email_required" if required else None
    return None if len(email) <= MAX_EMAIL and EMAIL_RE.match(email) else "auth.error.email"


def validate_password(password: str) -> str | None:
    return None if len(password or "") >= MIN_PASSWORD else "auth.error.password_short"
