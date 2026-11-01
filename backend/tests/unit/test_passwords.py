from argon2 import PasswordHasher

from explorai_backend.auth import passwords

PASSWORD = "correct horse battery staple"


def test_hash_is_argon2id_and_salted() -> None:
    first = passwords.hash_password(PASSWORD)
    second = passwords.hash_password(PASSWORD)

    assert first.startswith("$argon2id$")
    assert first != second  # a fresh random salt per hash
    assert PASSWORD not in first
    assert passwords.verify_password(first, PASSWORD)
    assert passwords.verify_password(second, PASSWORD)


def test_wrong_password_fails() -> None:
    stored = passwords.hash_password(PASSWORD)

    assert not passwords.verify_password(stored, PASSWORD + "!")


def test_malformed_stored_hash_fails_instead_of_raising() -> None:
    assert not passwords.verify_password("not-a-hash", PASSWORD)


def test_dummy_hash_has_the_same_cost_as_real_hashes() -> None:
    hasher = PasswordHasher()
    dummy = passwords.DUMMY_HASH

    assert dummy.startswith("$argon2id$")
    assert not hasher.check_needs_rehash(dummy)
    passwords.verify_dummy(PASSWORD)  # never raises, whatever the input
