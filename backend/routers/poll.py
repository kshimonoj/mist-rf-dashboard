from fastapi import APIRouter
from scheduler import poll_all_sites

router = APIRouter()


@router.post("/api/poll-now")
async def poll_now() -> dict:
    await poll_all_sites()
    return {"status": "ok"}
