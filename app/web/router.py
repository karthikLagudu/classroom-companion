from fastapi import APIRouter

from app.web.auth import router as auth_router
from app.web.coordinator import router as coordinator_router
from app.web.files import router as files_router
from app.web.student import router as student_router
from app.web.teacher import router as teacher_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(teacher_router)
router.include_router(student_router)
router.include_router(coordinator_router)
router.include_router(files_router)
