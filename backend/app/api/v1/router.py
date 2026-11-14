"""Router central v1."""

from fastapi import APIRouter

from app.api.v1 import (
    agenda,
    auth,
    branding,
    catalog,
    onboarding,
    permission_profiles,
    public,
    services,
    settings,
    staff,
    vehicles_catalog,
)

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
api_router.include_router(services.categories, prefix="/service-categories", tags=["Servicios"])
api_router.include_router(services.services, prefix="/services", tags=["Servicios"])
api_router.include_router(services.templates, prefix="/templates", tags=["Servicios"])
api_router.include_router(vehicles_catalog.makes, prefix="/vehicle-makes", tags=["Vehículos"])
api_router.include_router(vehicles_catalog.models, prefix="/vehicle-models", tags=["Vehículos"])
api_router.include_router(vehicles_catalog.colors, prefix="/vehicle-colors", tags=["Vehículos"])
api_router.include_router(
    vehicles_catalog.body_type_sizes, prefix="/body-type-sizes", tags=["Vehículos"]
)
api_router.include_router(branding.router, prefix="/branding", tags=["Personalización"])
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["Alta"])
api_router.include_router(agenda.router, prefix="/agenda", tags=["Agenda"])
api_router.include_router(public.router, prefix="/public", tags=["Turnero público"])
