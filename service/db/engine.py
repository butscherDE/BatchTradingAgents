from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from service.db.models import Base


def get_async_engine(db_path: str):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path}"
    return create_async_engine(url, echo=False)


def get_sync_engine(db_path: str):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path}"
    engine = create_engine(url, echo=False)
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode=WAL"))
        conn.commit()
    return engine


def get_async_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def get_sync_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


async def init_db(engine, db_path: str = "./data/service.db"):
    """Run Alembic migrations (creates tables if needed), then ensure all tables exist."""
    import asyncio
    await asyncio.to_thread(_run_migrations, db_path)

    # Fallback: create any tables not yet covered by migrations (new tables)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _run_migrations(db_path: str):
    """Run Alembic upgrade head synchronously."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception:
        pass
