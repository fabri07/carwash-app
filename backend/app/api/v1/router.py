"""Router central v1."""

from fastapi import APIRouter

from app.api.v1 import auth, permission_profiles, staff

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(
    permission_profiles.router, prefix="/permission-profiles", tags=["Equipo"]
)
api_router.include_router(staff.router, prefix="/staff", tags=["Equipo"])
