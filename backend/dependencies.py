from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import decode_access_token
from backend.database import get_db_session
from backend.models import User

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_optional_user(
    session: DbSession,
    access_token: Annotated[str | None, Cookie()] = None,
) -> User | None:
    if not access_token:
        return None
    user_id = decode_access_token(access_token)
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def require_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập")
    return user


async def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cần quyền admin")
    return user


CurrentUser = Annotated[User, Depends(require_user)]
CurrentAdmin = Annotated[User, Depends(require_admin)]
