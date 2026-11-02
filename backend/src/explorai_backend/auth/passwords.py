"""Password hashing with Argon2id.

Argon2id is memory-hard: each guess costs an attacker 64 MiB of memory as well
as CPU time (argon2-cffi's RFC 9106 low-memory defaults), which defeats the GPU
and ASIC farms that make fast hashes (SHA-256, MD5) crackable. Every hash gets
its own random salt, embedded in the encoded string with the parameters, so two
users with the same password get different hashes and precomputed tables are
useless.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Verified when a login names an unknown email, so that the answer takes as
# long as for a real account and timing does not reveal which emails exist.
# Hashed once at import, with the same parameters as real hashes.
DUMMY_HASH = _hasher.hash("explorai-dummy-password-never-valid")


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored: str, plain: str) -> bool:
    """Check ``plain`` against an encoded hash; never raises on a mismatch.

    The comparison inside argon2 is constant-time. A malformed stored hash is a
    failure, not an error, so a corrupt row can never let anyone in.
    """
    try:
        return _hasher.verify(stored, plain)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy(plain: str) -> None:
    """Spend the cost of one verification, for an email that has no account."""
    verify_password(DUMMY_HASH, plain)
