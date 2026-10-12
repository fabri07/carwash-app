"""Router central v1."""

from fastapi import APIRouter

from app.api.v1 import auth, catalog, permission_profiles, settings, staff

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(
    permission_profiles.router, prefix="/permission-profiles", tags=["Equipo"]
)
api_router.include_router(staff.router, prefix="/staff", tags=["Equipo"])
api_router.include_router(settings.router, prefix="/settings", tags=["Configuración"])
api_router.include_router(catalog.vehicle_sizes, prefix="/vehicle-sizes", tags=["Configuración"])
api_router.include_router(catalog.resources, prefix="/resources", tags=["Configuración"])
api_router.include_router(catalog.business_hours, prefix="/business-hours", tags=["Configuración"])
api_router.include_router(
    catalog.payment_methods, prefix="/payment-methods", tags=["Configuración"]
)
