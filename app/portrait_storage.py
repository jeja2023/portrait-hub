from typing import Any, Protocol

from app.portrait_gallery import GALLERY, load_gallery_state, save_gallery_state
from app.settings import PORTRAIT_STORAGE_BACKEND


class GalleryStore(Protocol):
    backend_name: str

    def health(self) -> dict[str, Any]:
        ...

    def save(self) -> None:
        ...

    def reload(self) -> None:
        ...


class JsonGalleryStore:
    backend_name = "json_file"

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "people": len(GALLERY), "status": "ready"}

    def save(self) -> None:
        save_gallery_state()

    def reload(self) -> None:
        load_gallery_state()


class PostgresGalleryStore:
    backend_name = "postgres"

    def health(self) -> dict[str, Any]:
        from app.portrait_postgres import postgres_health

        return {"backend": self.backend_name, **postgres_health()}

    def save(self) -> None:
        save_gallery_state()

    def reload(self) -> None:
        load_gallery_state()


def configured_gallery_store() -> GalleryStore:
    if PORTRAIT_STORAGE_BACKEND == "postgres":
        return PostgresGalleryStore()
    return JsonGalleryStore()


GALLERY_STORE = configured_gallery_store()


def store_backend_name() -> str:
    return GALLERY_STORE.backend_name
