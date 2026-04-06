"""SQLAlchemy ORM models package.

Import all model modules here so Alembic's env.py can discover them
via ``Base.metadata`` after importing this package.
"""

from app.database import Base  # noqa: F401 — re-exported for Alembic

__all__ = ["Base"]
