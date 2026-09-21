from fastapi import APIRouter

from app.api.routes import ai, auth, courses, dashboard, documents, knowledge, tasks

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(knowledge.router, tags=["knowledge"])
