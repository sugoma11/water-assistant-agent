"""Argon2 password hashing helpers (NFR1).

A single process-wide :class:`~argon2.PasswordHasher` with library defaults backs
both hashing and verification; ``verify_password`` swallows argon2's mismatch
exceptions and returns a plain bool so callers can keep a uniform 401 path.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2 hash (encoded string, includes salt + parameters)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return ``True`` iff *password* matches *password_hash*."""
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False
