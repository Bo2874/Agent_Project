"""Delete unpinned user memories whose retention deadline has passed."""

import asyncio

from backend.database import SessionFactory
from backend.services.memory import purge_expired_memories


async def main() -> None:
    async with SessionFactory() as session:
        deleted = await purge_expired_memories(session)
    print(f"Purged {deleted} expired user memories.")


if __name__ == "__main__":
    asyncio.run(main())
