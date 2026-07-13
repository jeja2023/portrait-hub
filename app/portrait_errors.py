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
        "description": "请求结构、查询边界或请求体验证失败。",
        "operator_action": "修正请求结构；如需追踪，请在日志中使用同一 request_id。",
    },
    {
        "code": "client_error",
        "http_status": 400,
        "retryable": False,
        "category": "client",
        "description": "参数不受支持、模型引用无效、内容长度格式错误或业务输入无效。",
        "operator_action": "修正客户端请求；不要用未改变的请求体重试。",
    },
    {
        "code": "unauthorized",
        "http_status": 401,
        "retryable": False,
        "category": "auth",
        "description": "Bearer 令牌、JWT 或应用接口密钥缺失或无效。",
        "operator_action": "重试前刷新凭证，并验证租户请求头。",
    },
    {
        "code": "forbidden",
        "http_status": 403,
        "retryable": False,
        "category": "auth",
        "description": "凭证有效，但缺少所需的 RBAC 权限范围或租户声明。",
        "operator_action": "为接入应用申请所需的最小权限范围。",
    },
    {
        "code": "not_found",
        "http_status": 404,
        "retryable": False,
        "category": "resource",
        "description": "请求的人员、任务、视频流、模型、别名、接入应用或事件回调在该租户中不存在。",
        "operator_action": "停止轮询，并重新读取租户范围内的资源列表。",
    },
    {
        "code": "conflict",
        "http_status": 409,
        "retryable": False,
        "category": "state",
        "description": "变更与当前状态冲突，例如模型发布时的别名目标预期不一致。",
        "operator_action": "读取最新状态后，带着更新后的预期重新提交变更。",
    },
    {
        "code": "too_large",
        "http_status": 413,
        "retryable": False,
        "category": "payload",
        "description": "上传文件、解码媒体、元数据或请求体超过配置限制。",
        "operator_action": "重试前压缩媒体或拆分请求。",
    },
    {
        "code": "batch_job_error",
        "http_status": 400,
        "retryable": False,
        "category": "job",
        "description": "批任务请求或执行前派生出的任务状态无效。",
        "operator_action": "提交其他变更前，先修正批处理参数或读取任务状态。",
    },    {
        "code": "rate_limited",
        "http_status": 429,
        "retryable": True,
        "category": "quota",
        "description": "全局或应用级令牌桶、每日配额已耗尽。",
        "operator_action": "遵守 Retry-After，使用指数退避，并降低客户端并发。",
    },
    {
        "code": "inference_error",
        "http_status": 500,
        "retryable": True,
        "category": "runtime",
        "description": "请求被接受后，模型执行或后处理失败。",
        "operator_action": "在预算内重试幂等请求；若反复出现，请向运维提供 request_id。",
    },
    {
        "code": "storage_error",
        "http_status": 503,
        "retryable": True,
        "category": "dependency",
        "description": "状态存储、对象存储、向量库、任务队列或外部依赖不可用。",
        "operator_action": "检查服务健康后，带退避重试幂等请求。",
    },
    {
        "code": "state_write_failed",
        "http_status": 503,
        "retryable": True,
        "category": "dependency",
        "description": "受保护的状态或审计写入失败，变更已被拒绝或回滚。",
        "operator_action": "除非后续读取证明已提交，否则按未提交处理该变更。",
    },
    {
        "code": "migration_error",
        "http_status": 500,
        "retryable": False,
        "category": "state",
        "description": "受控操作期间，迁移、备份或状态转换失败。",
        "operator_action": "停止依赖该状态的发布工作，完成状态对账后再重试。",
    },    {
        "code": "rollback_failed",
        "http_status": 500,
        "retryable": False,
        "category": "state",
        "description": "变更失败，且补偿路径也报告了失败。",
        "operator_action": "携带 request_id 升级处理；可能需要人工对账。",
    },
)


def error_code_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in ERROR_CODE_CATALOG]