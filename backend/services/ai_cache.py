"""
Shared AI response cache.

The single biggest cost lever: identical inputs never hit an external API twice.
Keyed by namespace + normalized input hash, stored in the ai_cache table.

  jobs pool        → 24h TTL (job listings live for weeks; 24h freshness is fine)
  interview_qs     → 30d TTL (question banks per role family barely change)
  company_intel    → 30d TTL (company facts are stable)
  jd_analysis      → 7d TTL  (same JD pasted by multiple users)
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, delete
from db.database import AsyncSessionLocal
from db.models import AICache
from loguru import logger


def make_key(namespace: str, *parts: str) -> str:
    normalized = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(f"{namespace}:{normalized}".encode()).hexdigest()


async def cache_get(namespace: str, *parts: str) -> Optional[Any]:
    key = make_key(namespace, *parts)
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AICache).where(AICache.key == key))).scalar_one_or_none()
            if not row:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                await db.delete(row)
                await db.commit()
                return None
            logger.info(f"AI cache HIT: {namespace} ({key[:12]})")
            return row.value_json
    except Exception as e:
        logger.warning(f"AI cache get failed: {e}")
        return None


async def cache_set(namespace: str, value: Any, ttl_hours: float, *parts: str) -> None:
    key = make_key(namespace, *parts)
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AICache).where(AICache.key == key))).scalar_one_or_none()
            expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            if row:
                row.value_json = value
                row.expires_at = expires
            else:
                db.add(AICache(key=key, namespace=namespace, value_json=value, expires_at=expires))
            await db.commit()
            logger.info(f"AI cache SET: {namespace} ttl={ttl_hours}h ({key[:12]})")
    except Exception as e:
        logger.warning(f"AI cache set failed: {e}")


async def cache_cleanup() -> int:
    """Delete expired rows. Called opportunistically at startup."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(delete(AICache).where(AICache.expires_at < datetime.now(timezone.utc)))
            await db.commit()
            return result.rowcount or 0
    except Exception:
        return 0
