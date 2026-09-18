from app.persistence.models.dummy_resource import DummyResource
from app.persistence.repositories.base import BaseRepository


class DummyResourceRepository(BaseRepository[DummyResource]):
    model = DummyResource
