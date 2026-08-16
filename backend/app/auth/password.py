"""Argon2 password hashing (argon2-cffi). Hashes are never logged or returned by
any endpoint — `UserRead` (app/schemas/user.py) simply has no field for one."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# A hash of a fixed, unguessable value — verified against on every login where the
# email doesn't exist, so that path costs the same wall-clock time as a real
# password check and an attacker can't distinguish "unknown email" from "wrong
# password" by timing (§4: login must not leak which emails exist).
_DUMMY_HASH = _hasher.hash("race-app-dummy-hash-for-timing-parity")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True if `password` matches `password_hash`. Never raises — a malformed or
    foreign hash is treated as a mismatch rather than propagating."""
    try:
        _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHash):
        return False
    return True


def verify_dummy(password: str) -> None:
    """Runs a real argon2 verify against a fixed dummy hash. Call this instead of
    `verify_password` when no user was found, so the login path takes comparable
    time whether or not the email exists."""
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except VerifyMismatchError:
        pass


def needs_rehash(password_hash: str) -> bool:
    """True if `password_hash` was produced with weaker parameters than the
    hasher's current defaults. Callers rehash and persist transparently on the
    next successful login."""
    return _hasher.check_needs_rehash(password_hash)
