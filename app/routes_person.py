from fastapi import APIRouter

from app.routes_person_tracks import router as tracks_router


router = APIRouter()
router.include_router(tracks_router)
