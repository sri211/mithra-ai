"""
One-off data migration: SQLite  ->  managed Postgres (Neon/Supabase).

SAFE BY DESIGN:
  - Reads from the existing SQLite file, writes to a FRESH Postgres DB.
  - Never touches / deletes the SQLite source.
  - Creates all tables on Postgres, copies every row in FK-dependency order,
    then prints a per-table source-vs-dest row-count table so we can confirm
    nothing was lost before flipping DATABASE_URL.

Run INSIDE the api container (has the models, the SQLite volume, and network
to Postgres):

    docker exec -e PG_URL='postgresql://USER:PASS@HOST/DB?sslmode=require' \
      $(docker ps -q --filter name=api) \
      python scripts/migrate_to_postgres.py

SQLITE_URL defaults to the production path; override with -e SQLITE_URL=... .
"""
import asyncio
import os
import sys

# ensure the app package is importable when run as a script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import select, func

from db.database import Base, _normalize_db_url
import db.models  # noqa: F401 — registers every table on Base.metadata

SQLITE_URL = os.getenv("SQLITE_URL", "sqlite+aiosqlite:////app/data/mithra.db")
PG_RAW = os.environ.get("PG_URL") or os.environ.get("DATABASE_URL")


async def _count(engine, table) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(select(func.count()).select_from(table))).scalar() or 0


async def main():
    if not PG_RAW or "postgres" not in PG_RAW:
        print("ERROR: set PG_URL to your Postgres connection string.")
        sys.exit(1)

    pg_url, pg_args = _normalize_db_url(PG_RAW)
    src = create_async_engine(SQLITE_URL)
    dst = create_async_engine(pg_url, connect_args=pg_args)

    print(f"Source: {SQLITE_URL}")
    print(f"Dest:   {pg_url}\n")

    # 1. create schema on Postgres
    async with dst.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[schema] created all tables on Postgres\n")

    # 2. copy rows table-by-table in FK-safe order
    report = []
    for table in Base.metadata.sorted_tables:
        async with src.connect() as sconn:
            rows = [dict(r) for r in (await sconn.execute(table.select())).mappings().all()]
        if rows:
            async with dst.begin() as dconn:
                # chunk to keep statements reasonable
                for i in range(0, len(rows), 500):
                    await dconn.execute(table.insert(), rows[i:i + 500])
        print(f"[copy] {table.name}: {len(rows)} rows")

    # 3. verify counts match
    print("\n=== VERIFY (source vs dest) ===")
    ok = True
    for table in Base.metadata.sorted_tables:
        s = await _count(src, table)
        d = await _count(dst, table)
        mark = "OK " if s == d else "!! "
        if s != d:
            ok = False
        report.append((mark, table.name, s, d))
        print(f"{mark} {table.name:24s} src={s:6d}  dst={d:6d}")

    await src.dispose()
    await dst.dispose()
    print("\nRESULT:", "ALL COUNTS MATCH — safe to switch DATABASE_URL" if ok
          else "MISMATCH — DO NOT switch; investigate above")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
