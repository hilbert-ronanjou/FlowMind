from fastapi import APIRouter

from app.api.routes import auth, courses, dashboard, tasks

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])

