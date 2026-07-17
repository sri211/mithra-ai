from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from middleware.auth import get_optional_user
from agents.company_intel_agent import get_company_intel, suggest_companies

router = APIRouter()


@router.get("/suggest")
async def suggest(q: str = ""):
    """Free typeahead — Wikipedia-backed, cached 30d. No credits."""
    return {"results": await suggest_companies(q)}


@router.get("/intel")
async def intel(
    name: str = "",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Full company dossier. First lookup for a company costs 1 cheap AI call and
    is then cached 30 days for EVERY user — so it's effectively free to serve."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name required")

    # Charge a token amount only when we're likely to do AI work (cache miss).
    # Cached companies cost nothing to serve, so they cost the user nothing.
    from services.ai_cache import cache_get
    is_cached = bool(await cache_get("company_intel_v2", name))
    if current_user and not is_cached:
        from services.credits import charge
        await charge(current_user, db, "company_intel")

    data = await get_company_intel(name)
    if not data:
        raise HTTPException(status_code=404, detail="Company not found")
    return data
