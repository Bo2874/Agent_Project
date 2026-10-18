import argparse
import asyncio
from getpass import getpass

from pydantic import EmailStr, TypeAdapter
from sqlalchemy.exc import IntegrityError

from backend.core.security import hash_password
from backend.database import SessionFactory, engine
from backend.models import User
from backend.services.auth import get_user_by_email


async def create_admin(email: str, full_name: str | None, password: str) -> None:
    normalized_email = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    async with SessionFactory() as session:
        existing = await get_user_by_email(session, normalized_email)
        if existing:
            existing.role = "admin"
            existing.full_name = full_name or existing.full_name
            existing.password_hash = hash_password(password)
            existing.is_active = True
            action = "Đã cập nhật"
        else:
            session.add(
                User(
                    email=normalized_email,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    role="admin",
                )
            )
            action = "Đã tạo"
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise SystemExit("Không thể tạo admin vì email đã tồn tại.") from None
    await engine.dispose()
    print(f"{action} tài khoản admin: {normalized_email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo hoặc cập nhật tài khoản admin.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    password = getpass("Mật khẩu admin (ít nhất 8 ký tự): ")
    confirmation = getpass("Nhập lại mật khẩu: ")
    if password != confirmation:
        raise SystemExit("Hai mật khẩu không khớp.")
    if len(password) < 8 or len(password) > 128:
        raise SystemExit("Mật khẩu phải dài từ 8 đến 128 ký tự.")
    asyncio.run(create_admin(args.email, args.name, password))


if __name__ == "__main__":
    main()
