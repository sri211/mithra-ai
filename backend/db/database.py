from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./mithra.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def init_db():
    async with engine.begin() as conn:
        # Create any missing tables
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate: add any missing columns to existing tables
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn):
    """
    Safe column migration for SQLite.
    Adds any columns defined in SQLAlchemy models that don't yet exist in the DB.
    Runs at every startup — idempotent.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(conn)
    for table in Base.metadata.tables.values():
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                col_type = col.type.compile(dialect=conn.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default = f"DEFAULT {col.default.arg!r}" if col.default and col.default.arg is not None else ""
                sql = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type} {nullable} {default}".strip()
                try:
                    conn.execute(text(sql))
                    print(f"[DB migration] Added column: {table.name}.{col.name}")
                except Exception as e:
                    print(f"[DB migration] Skip {table.name}.{col.name}: {e}")

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
