from __future__ import annotations

from typing import Any


class PortraitError(Exception):
    status_code = 500
    code = "portrait_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def public_detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": self.message, "code": self.code}
        if self.details:
            payload["details"] = self.details
        return payload


class GalleryError(PortraitError):
    status_code = 400
    code = "gallery_error"


class InferenceError(PortraitError):
    status_code = 500
    code = "inference_error"


class StorageError(PortraitError):
    status_code = 503
    code = "storage_error"


class BatchJobError(PortraitError):
    status_code = 400
    code = "batch_job_error"


class MigrationError(PortraitError):
    status_code = 500
    code = "migration_error"
