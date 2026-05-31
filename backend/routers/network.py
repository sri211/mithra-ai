from fastapi import APIRouter
from pydantic import BaseModel
from agents.network_agent import find_connections, draft_outreach

router = APIRouter()


class NetworkRequest(BaseModel):
    company: str
    target_role: str
    user_profile: dict = {}


class OutreachRequest(BaseModel):
    person: dict
    user_profile: dict
    context: str = ""


@router.post("/find")
async def find(req: NetworkRequest):
    result = await find_connections(req.company, req.target_role, req.user_profile)
    return result


@router.post("/outreach")
async def outreach(req: OutreachRequest):
    message = await draft_outreach(req.person, req.user_profile, req.context)
    return {"message": message}
