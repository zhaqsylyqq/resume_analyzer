"""SEC-01: time-based one-time passwords (RFC 6238) and recovery codes.

Works with any authenticator app (Google Authenticator, Microsoft Authenticator, Aegis…):
6 digits, 30-second steps, HMAC-SHA1 — the defaults every app supports. Implemented with
the standard library; only the QR picture uses the small `segno` package.

A code is accepted for the previous, current and next step (clock drift of ±30 s), and each
step only once: the last used step is stored, so a code seen over someone's shoulder cannot
be replayed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODES = 10
ISSUER = "Enbek Screen"


def generate_secret() -> str:
    """160 random bits, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The matched step when `code` is valid and newer than `last_step`; None otherwise."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    params = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{params}"


def qr_svg_data_uri(uri: str) -> str:
    """The provisioning URI as an inline SVG QR code (allowed by the CSP: img-src data:)."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#111", light="#fff")


def format_secret(secret: str) -> str:
    """Groups of four for typing the key by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# Recovery codes ----------------------------------------------------------------------------

def generate_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """One-time codes like 'k7m2-9xq4-pt3w' (about 60 bits each)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(count)]


def _normalize_recovery(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    # The codes are long random strings, so a plain SHA-256 is enough (no dictionary to try).
    return hashlib.sha256(_normalize_recovery(code).encode()).hexdigest()


def use_recovery_code(code: str, hashes: list[str]) -> list[str] | None:
    """The remaining hashes when `code` matches one of them; None when it matches none."""
    candidate = hash_recovery_code(code)
    for i, stored in enumerate(hashes):
        if hmac.compare_digest(candidate, stored):
            return hashes[:i] + hashes[i + 1:]
    return None
