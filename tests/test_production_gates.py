from app import production_gates, settings


def test_production_profile_requires_external_services(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PORTRAIT_RUNTIME_PROFILE", "production")
    monkeypatch.setattr(settings, "PRODUCTION_EXTERNAL_SERVICES_REQUIRED", True)
    monkeypatch.setattr(settings, "API_TOKEN", "platform-token")
    monkeypatch.setattr(settings, "API_TOKEN_TENANT_ID", "")
    monkeypatch.setattr(settings, "API_TOKEN_ALLOW_TENANT_OVERRIDE", False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    monkeypatch.setattr(settings, "DEBUG_ENDPOINTS_ENABLED", True)
    monkeypatch.setattr(settings, "ENABLE_API_DOCS", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 768 * 1024 * 1024)
    monkeypatch.setattr(settings, "MAX_VIDEO_BYTES", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(settings, "POSTGRES_DSN", "")
    monkeypatch.setattr(settings, "PORTRAIT_VECTOR_BACKEND", "local")
    monkeypatch.setattr(settings, "PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND", False)
    monkeypatch.setattr(settings, "PORTRAIT_OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "S3_REGION", "")
    monkeypatch.setattr(settings, "TASK_QUEUE_BACKEND", "local")
    monkeypatch.setattr(settings, "VIDEO_JOB_WORKER_IN_PROCESS", True)
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "OPENTELEMETRY_ENABLED", False)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
    monkeypatch.setattr(settings, "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", False)
    monkeypatch.setattr(production_gates, "postgres_driver_available", lambda: False)
    monkeypatch.setattr(production_gates, "postgres_pool_available", lambda: False)
    monkeypatch.setattr(production_gates, "boto3", None)
    monkeypatch.setattr(production_gates, "redis", None)
    monkeypatch.setattr(production_gates, "optional_module_available", lambda _name: False)

    failures = production_gates.production_externalization_failures()

    assert "生产环境中 AUTH_REQUIRED 必须为 true" in failures
    assert "生产环境中 DEBUG_ENDPOINTS_ENABLED 必须为 false" in failures
    assert "生产环境中 ENABLE_API_DOCS 必须为 false" in failures
    assert "生产环境中 RATE_LIMIT_PER_MINUTE 必须大于 0" in failures
    assert any("MAX_REQUEST_BODY_BYTES" in failure for failure in failures)
    assert "生产环境中 PORTRAIT_STORAGE_BACKEND 必须为 postgres" in failures
    assert "生产环境中 PORTRAIT_VECTOR_BACKEND 必须为 pgvector 或 qdrant" in failures
    assert "生产环境中 PORTRAIT_OBJECT_STORAGE_BACKEND 必须为 s3" in failures
    assert "生产环境中 TASK_QUEUE_BACKEND 必须为 redis" in failures
    assert any("API_TOKEN_TENANT_ID" in failure for failure in failures)
    assert "生产环境中 VIDEO_JOB_WORKER_IN_PROCESS 必须为 false" in failures
    assert "生产环境中 OPENTELEMETRY_ENABLED 必须为 true" in failures


def test_production_profile_accepts_externalized_pgvector_stack(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PORTRAIT_RUNTIME_PROFILE", "prod")
    monkeypatch.setattr(settings, "PRODUCTION_EXTERNAL_SERVICES_REQUIRED", True)
    monkeypatch.setattr(settings, "API_TOKEN", "platform-token")
    monkeypatch.setattr(settings, "API_TOKEN_TENANT_ID", "tenant-a")
    monkeypatch.setattr(settings, "API_TOKEN_ALLOW_TENANT_OVERRIDE", False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "RBAC_ENABLED", False)
    monkeypatch.setattr(settings, "DEBUG_ENDPOINTS_ENABLED", False)
    monkeypatch.setattr(settings, "ENABLE_API_DOCS", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 120)
    monkeypatch.setattr(settings, "MAX_VIDEO_BYTES", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "MAX_REQUEST_BODY_BYTES", 112 * 1024 * 1024)
    monkeypatch.setattr(settings, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(settings, "POSTGRES_DSN", "postgresql://portrait:secret@db/portrait")
    monkeypatch.setattr(settings, "PORTRAIT_VECTOR_BACKEND", "pgvector")
    monkeypatch.setattr(settings, "PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND", True)
    monkeypatch.setattr(settings, "PORTRAIT_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "portrait-prod")
    monkeypatch.setattr(settings, "S3_REGION", "us-east-1")
    monkeypatch.setattr(settings, "TASK_QUEUE_BACKEND", "redis")
    monkeypatch.setattr(settings, "VIDEO_JOB_WORKER_IN_PROCESS", False)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(settings, "OPENTELEMETRY_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318/v1/traces")
    monkeypatch.setattr(settings, "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", True)
    monkeypatch.setattr(production_gates, "postgres_driver_available", lambda: True)
    monkeypatch.setattr(production_gates, "postgres_pool_available", lambda: True)
    monkeypatch.setattr(production_gates, "boto3", object())
    monkeypatch.setattr(production_gates, "redis", object())
    monkeypatch.setattr(production_gates, "optional_module_available", lambda _name: True)

    assert production_gates.production_externalization_failures() == []
    production_gates.validate_production_externalization()


def test_production_profile_requires_credential_backend(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PORTRAIT_RUNTIME_PROFILE", "production")
    monkeypatch.setattr(settings, "PRODUCTION_EXTERNAL_SERVICES_REQUIRED", True)
    monkeypatch.setattr(settings, "API_TOKEN", "")
    monkeypatch.setattr(settings, "RBAC_ENABLED", False)

    failures = production_gates.production_externalization_failures()

    assert "生产环境必须配置 API_TOKEN 或启用 RBAC_ENABLED" in failures


def test_optional_module_available_treats_missing_parent_as_false(monkeypatch) -> None:
    def raise_missing_parent(_module_name: str):
        raise ModuleNotFoundError("No module named 'missing_parent'")

    monkeypatch.setattr(production_gates.importlib.util, "find_spec", raise_missing_parent)

    assert production_gates.optional_module_available("missing_parent.child") is False