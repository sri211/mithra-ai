from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import ssl
import os


def _ssl_require_ctx():
    """TLS WITHOUT CA verification — matches libpq `sslmode=require` (encrypt the
    connection, don't verify the chain). Supabase/Neon poolers present certs that
    don't chain to the container's default CA bundle, so full verify fails."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./mithra.db")


def _normalize_db_url(url: str):
    """
    Accept the connection strings people actually paste and return an
    (async-driver URL, connect_args) pair.

      - Neon/Supabase give `postgres://` or `postgresql://...?sslmode=require`.
        SQLAlchemy async needs the `postgresql+asyncpg://` driver, and asyncpg
        does NOT understand the libpq `sslmode` query param — it wants an `ssl`
        connect-arg instead. So we rewrite the scheme, strip `sslmode`/`channel_binding`,
        and require SSL via connect_args (managed Postgres always needs TLS).
      - SQLite (and anything already using an explicit +driver) passes through.
    """
    connect_args: dict = {}
    if url.startswith("postgres://") or url.startswith("postgresql://") or url.startswith("postgresql+asyncpg://"):
        parts = urlsplit(url)
        scheme = "postgresql+asyncpg"
        # drop libpq-only params asyncpg rejects; remember if SSL was requested
        q = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("sslmode", "channel_binding")]
        url = urlunsplit((scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        connect_args["ssl"] = _ssl_require_ctx()  # managed Postgres requires TLS (require-level)
        # Supabase / pgbouncer poolers reuse backend connections, which collides
        # with asyncpg's server-side prepared statements ("prepared statement
        # __asyncpg_... already exists"). Disable statement caching for poolers.
        host = parts.hostname or ""
        if "pooler." in host or "pgbouncer" in host:
            connect_args["statement_cache_size"] = 0  # asyncpg connect() kwarg
    return url, connect_args


DATABASE_URL, _CONNECT_ARGS = _normalize_db_url(RAW_DATABASE_URL)
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_CONNECT_ARGS,
    # Postgres benefits from pooling with pre-ping to survive idle disconnects
    # (Neon/Supabase drop idle connections); SQLite ignores these.
    **({} if IS_SQLITE else {"pool_pre_ping": True, "pool_recycle": 300}),
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# Postgres: create_all does NOT add columns to already-existing tables, so new
# columns on existing models must be ALTERed in explicitly (idempotent).
_PG_COLUMN_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_seen INTEGER DEFAULT 0",
]


async def init_db():
    async with engine.begin() as conn:
        # Create any missing tables
        await conn.run_sync(Base.metadata.create_all)
        if IS_SQLITE:
            # Auto-migrate columns (SQLite-flavoured ALTER).
            await conn.run_sync(_add_missing_columns)
        else:
            from sqlalchemy import text
            for ddl in _PG_COLUMN_MIGRATIONS:
                try:
                    await conn.execute(text(ddl))
                except Exception as e:
                    print(f"[DB migration] {ddl}: {e}")


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
