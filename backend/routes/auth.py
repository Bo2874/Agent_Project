from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from backend.core.config import get_settings
from backend.core.security import create_access_token
from backend.dependencies import CurrentUser, DbSession
from backend.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, UserResponse
from backend.services.auth import authenticate_user, get_user_by_email, register_user

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=settings.auth_token_expire_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, response: Response, session: DbSession) -> LoginResponse:
    if await get_user_by_email(session, str(data.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email đã được sử dụng")
    try:
        user = await register_user(session, data)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email đã được sử dụng",
        ) from exc
    set_auth_cookie(response, create_access_token(user.id, user.role))
    return LoginResponse(user=UserResponse.model_validate(user), message="Đăng ký thành công")


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, response: Response, session: DbSession) -> LoginResponse:
    user = await authenticate_user(session, str(data.email), data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không đúng",
        )
    set_auth_cookie(response, create_access_token(user.id, user.role))
    return LoginResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
