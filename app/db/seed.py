"""First-run admin seeding.

If `SEED_ADMIN_USERNAME` and `SEED_ADMIN_PASSWORD` are set, ensure an admin user
with those credentials exists (idempotent — won't overwrite an existing user's
password). Skipped entirely if either value is blank.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import Role
from app.db import crud

logger = logging.getLogger(__name__)


async def seed_admin(db: AsyncSession) -> None:
    username = settings.SEED_ADMIN_USERNAME.strip()
    password = settings.SEED_ADMIN_PASSWORD
    if not username or not password:
        logger.info("Skipping admin seeding (no SEED_ADMIN_* configured).")
        return

    existing = await crud.get_user_by_username(db, username)
    if existing is not None:
        logger.info("Seeded admin user %r already exists; leaving it as-is.", username)
        return

    await crud.create_user(db, username=username, password=password, role=Role.ADMIN.value)
    logger.warning("Created seeded admin user %r — please change its password!", username)
