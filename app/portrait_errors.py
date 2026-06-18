from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class PortraitError(HTTPException):
    status_code = 500
    code = "portrait_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message
        self.details = details or {}
        self.code = getattr(type(self), "code", "portrait_error")
        effective_status_code = int(status_code if status_code is not None else type(self).status_code)
        super().__init__(status_code=effective_status_code, detail=self.public_detail(), headers=headers)

    def public_detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": self.message, "code": self.code}
        if self.details:
            payload["details"] = self.details
        return payload


class ClientError(PortraitError):
    status_code = 400
    code = "client_error"


class ValidationError(ClientError):
    code = "validation_error"


class NotFoundError(ClientError):
    status_code = 404
    code = "not_found"


class ConflictError(ClientError):
    status_code = 409
    code = "conflict"


class TooLargeError(ClientError):
    status_code = 413
    code = "too_large"


class UnauthorizedError(ClientError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ClientError):
    status_code = 403
    code = "forbidden"


class InferenceError(PortraitError):
    status_code = 500
    code = "inference_error"


class StorageError(PortraitError):
    status_code = 503
    code = "storage_error"


class BatchJobError(ClientError):
    code = "batch_job_error"


class MigrationError(PortraitError):
    status_code = 500
    code = "migration_error"
