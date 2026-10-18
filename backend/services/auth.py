from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from backend.models import User
from backend.schemas.auth import RegisterRequest


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def register_user(session: AsyncSession, data: RegisterRequest) -> User:
    user = User(
        email=str(data.email).lower(),
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role="user",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    return user
