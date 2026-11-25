"""SEC-02b: encryption of personal data in database columns (AES-256-GCM).

A stolen database, a raw `pg_dump` or a copy of the SQLite file shows only ciphertext for
the resume text, the file name, the candidate profile (name, contacts), demographics and
the 2FA secret. The key never goes into the database or the backups.

Text values are stored as  enc1:<key id>:<base64url(nonce | ciphertext | tag)>
Byte values (the uploaded file)  b"ENC1" + <key id> + nonce + ciphertext | tag

* The column name is bound to every value (associated data), so a value copied into
  another column does not decrypt.
* The key id is the first 8 hex digits of SHA-256(key): several keys can be configured at
  once (DATA_KEY = current, DATA_KEY_OLD = older ones), which is how keys are rotated.
* Values without the prefix are plaintext from before SEC-02b and are returned as they are;
  `seal_all` (start-up, `flask --app app data-key-rotate`) encrypts them with the current key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc1:"
BYTES_MAGIC = b"ENC1"
NONCE_BYTES = 12
KEY_FILE = ".data_key"


class DataKeyError(RuntimeError):
    """A key is missing, malformed or wrong for a stored value."""


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def parse_key(value: str, name: str = "DATA_KEY") -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.strip().encode())
    except (ValueError, binascii.Error) as exc:
        raise DataKeyError(f"{name} is not valid base64 (create one with `flask --app app generate-data-key`)") from exc
    if len(key) != 32:
        raise DataKeyError(f"{name} must be 32 bytes (create one with `flask --app app generate-data-key`)")
    return key


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


@dataclass
class Keyring:
    current: bytes
    old: list[bytes] = field(default_factory=list)
    _by_id: dict[str, bytes] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {key_id(k): k for k in [*self.old, self.current]}

    @property
    def current_id(self) -> str:
        return key_id(self.current)

    def key(self, kid: str) -> bytes:
        try:
            return self._by_id[kid]
        except KeyError:
            raise DataKeyError(f"data key {kid} is not configured (add it to DATA_KEY_OLD)") from None

    @classmethod
    def from_values(cls, current: str, old: Iterable[str] = ()) -> Keyring:
        return cls(parse_key(current), [parse_key(v, "DATA_KEY_OLD") for v in old if v.strip()])


# The keyring of this process, set by the app factory (`configure`). Storage functions run
# in requests, workers and CLI commands alike; all of them start from create_app().
_keyring: Keyring | None = None


def configure(keyring: Keyring | None) -> None:
    global _keyring
    _keyring = keyring


def keyring() -> Keyring:
    if _keyring is None:
        raise DataKeyError("field encryption is not configured (create_app sets it up)")
    return _keyring


def load_key_file(instance_path: str, create: bool) -> str | None:
    """The generated key kept in the data folder (development and tests). None if absent."""
    path = Path(instance_path) / KEY_FILE
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    if not create:
        return None
    value = generate_key()
    path.write_text(value, encoding="utf-8")
    return value


# Text ----------------------------------------------------------------------------------------

def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str | None, column: str) -> str | None:
    """Encrypt a text value for `column` (e.g. "resumes.text"). None and "" stay as they are."""
    if value is None or value == "" or is_sealed(value):
        return value
    ring = keyring()
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(ring.current).encrypt(nonce, value.encode("utf-8"), column.encode())
    return f"{PREFIX}{ring.current_id}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"


def decrypt(value: object, column: str) -> str | None:
    """The plain text of a stored value; plaintext from before SEC-02b passes through."""
    if value is None:
        return None
    text = str(value)
    if not text.startswith(PREFIX):
        return text
    kid, _, body = text[len(PREFIX):].partition(":")
    try:
        blob = base64.urlsafe_b64decode(body.encode())
        return AESGCM(keyring().key(kid)).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], column.encode()).decode("utf-8")
    except (InvalidTag, ValueError, binascii.Error) as exc:
        raise DataKeyError(f"cannot decrypt {column}: wrong key or damaged value") from exc


def sealed_with(value: object) -> str | None:
    """Key id of a stored text value, or None for plaintext."""
    if not is_sealed(value):
        return None
    return str(value)[len(PREFIX):].partition(":")[0]


# Bytes ---------------------------------------------------------------------------------------

def encrypt_bytes(value: bytes | None, column: str) -> bytes | None:
    if value is None or value.startswith(BYTES_MAGIC):
        return value
    ring = keyring()
    nonce = secrets.token_bytes(NONCE_BYTES)
    return (BYTES_MAGIC + ring.current_id.encode() + nonce
            + AESGCM(ring.current).encrypt(nonce, value, column.encode()))


def decrypt_bytes(value: bytes | None, column: str) -> bytes | None:
    if value is None or not value.startswith(BYTES_MAGIC):
        return value
    head = len(BYTES_MAGIC)
    kid = value[head:head + 8].decode("ascii", "replace")
    nonce = value[head + 8:head + 8 + NONCE_BYTES]
    try:
        return AESGCM(keyring().key(kid)).decrypt(nonce, value[head + 8 + NONCE_BYTES:], column.encode())
    except InvalidTag as exc:
        raise DataKeyError(f"cannot decrypt {column}: wrong key or damaged value") from exc
