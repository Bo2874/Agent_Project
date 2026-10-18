import uuid

from backend.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_is_hashed_and_can_be_verified() -> None:
    password = "a-valid-password"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_access_token_contains_user_identity() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "user")

    assert decode_access_token(token) == user_id
    assert decode_access_token(f"{token}changed") is None
