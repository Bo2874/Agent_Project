import uuid

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.security import hash_password
from backend.models import User

TEST_PASSWORD = "Test@123456"
TEST_ACCOUNTS = (
    ("user@personalagent.vn", "Test User", "user"),
    ("admin@personalagent.vn", "Test Admin", "admin"),
)
LEGACY_TEST_EMAILS = ("user@test.local", "admin@test.local")


def main() -> None:
    engine = create_engine(get_settings().migration_database_url)
    with Session(engine) as session:
        session.execute(delete(User).where(User.email.in_(LEGACY_TEST_EMAILS)))
        for email, full_name, role in TEST_ACCOUNTS:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(id=uuid.uuid4(), email=email, password_hash="")
                session.add(user)
            user.full_name = full_name
            user.password_hash = hash_password(TEST_PASSWORD)
            user.role = role
            user.is_active = True
        session.commit()

        created = session.scalars(
            select(User).where(User.email.in_([account[0] for account in TEST_ACCOUNTS]))
        ).all()
        for user in sorted(created, key=lambda item: item.role, reverse=True):
            print(f"{user.email}: {user.role}")
    engine.dispose()


if __name__ == "__main__":
    main()
