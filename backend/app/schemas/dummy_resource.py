import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import CamelModel


class DummyResourceCreate(BaseModel):
    # `extra="ignore"`: un `tenant_id` inyectado en el body se descarta en silencio.
    # El tenant sale del token, nunca del cuerpo.
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)


class DummyResourceUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)


class DummyResourceResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
