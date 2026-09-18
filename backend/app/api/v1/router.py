"""Router central v1: `auth` y `dummy-resources`."""

from fastapi import APIRouter

from app.api.v1 import auth, dummy_resources

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(
    dummy_resources.router, prefix="/dummy-resources", tags=["Dummy resources"]
)
