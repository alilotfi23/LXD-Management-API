"""SQLAlchemy ORM models.

Only the *local* concerns of this API are persisted here — namely the user
accounts used for JWT auth. Everything about LXD itself (instances, pools, ...)
lives in LXD's own state and is accessed live over the REST API; we do not
mirror it into this database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    """A user account of this API (used to mint JWTs).

    Roles follow the hierarchy in `app.core.security.Role`:
        admin > operator > viewer
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # bcrypt hash, never the plaintext password.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored as the Role enum's value ("viewer" | "operator" | "admin").
    role: Mapped[str] = mapped_column(String(16), default="viewer", nullable=False)
    # Soft flag so admins can disable a user without deleting the row.
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r} role={self.role}>"
