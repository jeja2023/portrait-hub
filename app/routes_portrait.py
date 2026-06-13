from fastapi import APIRouter

from app.routes_portrait_admin import router as admin_router
from app.routes_portrait_compare import router as compare_router
from app.routes_portrait_console import router as console_router
from app.routes_portrait_gallery import router as gallery_router
from app.routes_portrait_infer import router as infer_router
from app.routes_portrait_jobs import router as jobs_router
from app.routes_portrait_models import router as models_router
from app.routes_portrait_streams import router as streams_router


router = APIRouter()
router.include_router(infer_router)
router.include_router(compare_router)
router.include_router(gallery_router)
router.include_router(jobs_router)
router.include_router(streams_router)
router.include_router(models_router)
router.include_router(admin_router)
router.include_router(console_router)
