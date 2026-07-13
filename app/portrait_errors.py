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

ERROR_CODE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "code": "validation_error",
        "http_status": 422,
        "retryable": False,
        "category": "client",
        "description": "Request schema, query bounds, or body validation failed.",
        "operator_action": "Fix the request shape and retry with the same request_id in logs for traceability.",
    },
    {
        "code": "client_error",
        "http_status": 400,
        "retryable": False,
        "category": "client",
        "description": "Unsupported parameter, invalid model reference, malformed content length, or invalid business input.",
        "operator_action": "Correct the client request; do not retry unchanged payloads.",
    },
    {
        "code": "unauthorized",
        "http_status": 401,
        "retryable": False,
        "category": "auth",
        "description": "Bearer token, JWT, or application API key is missing or invalid.",
        "operator_action": "Refresh credentials and verify the tenant header before retrying.",
    },
    {
        "code": "forbidden",
        "http_status": 403,
        "retryable": False,
        "category": "auth",
        "description": "The credential is valid but lacks the required RBAC scope or tenant claim.",
        "operator_action": "Request the minimum required scope for the integration application.",
    },
    {
        "code": "not_found",
        "http_status": 404,
        "retryable": False,
        "category": "resource",
        "description": "The requested person, job, stream, model, alias, access application, or webhook does not exist in this tenant.",
        "operator_action": "Stop polling and re-read the tenant-scoped resource list.",
    },
    {
        "code": "conflict",
        "http_status": 409,
        "retryable": False,
        "category": "state",
        "description": "The mutation conflicts with current state, such as alias target expectations during rollout.",
        "operator_action": "Read the latest state and submit a new mutation with updated expectations.",
    },
    {
        "code": "too_large",
        "http_status": 413,
        "retryable": False,
        "category": "payload",
        "description": "Uploaded files, decoded media, metadata, or request body exceed configured limits.",
        "operator_action": "Compress media or split the request before retrying.",
    },
    {
        "code": "batch_job_error",
        "http_status": 400,
        "retryable": False,
        "category": "job",
        "description": "Batch job request or derived job state is invalid before execution starts.",
        "operator_action": "Correct the batch parameters or read the job status before submitting another mutation.",
    },    {
        "code": "rate_limited",
        "http_status": 429,
        "retryable": True,
        "category": "quota",
        "description": "Global or application-level token bucket or daily quota is exhausted.",
        "operator_action": "Honor Retry-After, use exponential backoff, and reduce client concurrency.",
    },
    {
        "code": "inference_error",
        "http_status": 500,
        "retryable": True,
        "category": "runtime",
        "description": "Model execution or post-processing failed after the request was accepted.",
        "operator_action": "Retry idempotent requests within budget and provide request_id to operations if repeated.",
    },
    {
        "code": "storage_error",
        "http_status": 503,
        "retryable": True,
        "category": "dependency",
        "description": "State, object storage, vector store, task queue, or external dependency is unavailable.",
        "operator_action": "Retry idempotent requests with backoff after checking service health.",
    },
    {
        "code": "state_write_failed",
        "http_status": 503,
        "retryable": True,
        "category": "dependency",
        "description": "A protected state or audit write failed and the mutation was rejected or rolled back.",
        "operator_action": "Treat the mutation as not committed unless a later read proves otherwise.",
    },
    {
        "code": "migration_error",
        "http_status": 500,
        "retryable": False,
        "category": "state",
        "description": "Migration, backup, or state transition failed during a controlled operation.",
        "operator_action": "Stop dependent rollout work and reconcile state before retrying the operation.",
    },    {
        "code": "rollback_failed",
        "http_status": 500,
        "retryable": False,
        "category": "state",
        "description": "A mutation failed and its compensation path also reported failures.",
        "operator_action": "Escalate with request_id; manual reconciliation may be required.",
    },
)


def error_code_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in ERROR_CODE_CATALOG]