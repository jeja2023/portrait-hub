from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS") != "1",
    reason="set PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS=1 to run external service testcontainers integration tests",
)


def test_postgres_container_gallery_round_trip() -> None:
    pytest.importorskip("testcontainers.postgres")
    pytest.importorskip("psycopg")
    pytest.importorskip("pgvector")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        assert dsn.startswith("postgres")


def test_qdrant_container_health_round_trip() -> None:
    pytest.importorskip("testcontainers.core.container")
    pytest.importorskip("qdrant_client")

    from qdrant_client import QdrantClient
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    with DockerContainer("qdrant/qdrant:v1.9.7").with_exposed_ports(6333) as container:
        wait_for_logs(container, "Qdrant HTTP listening on")
        port = container.get_exposed_port(6333)
        client = QdrantClient(url=f"http://127.0.0.1:{port}")
        assert client.get_collections() is not None


def test_redis_container_queue_round_trip() -> None:
    pytest.importorskip("testcontainers.core.container")
    redis_module = pytest.importorskip("redis")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    with DockerContainer("redis:7-alpine").with_exposed_ports(6379) as container:
        wait_for_logs(container, "Ready to accept connections")
        port = container.get_exposed_port(6379)
        client = redis_module.Redis.from_url(f"redis://127.0.0.1:{port}/0", decode_responses=True)
        assert client.ping() is True
        assert client.lpush("portrait:test", "message") == 1
        assert client.rpop("portrait:test") == "message"


def test_minio_container_s3_round_trip() -> None:
    pytest.importorskip("testcontainers.core.container")
    boto3 = pytest.importorskip("boto3")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    access_key = "portrait"
    secret_key = "portrait-secret"
    with (
        DockerContainer("minio/minio:RELEASE.2025-04-22T22-12-26Z")
        .with_env("MINIO_ROOT_USER", access_key)
        .with_env("MINIO_ROOT_PASSWORD", secret_key)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000) as container
    ):
        wait_for_logs(container, "API:")
        port = container.get_exposed_port(9000)
        client = boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{port}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )
        client.create_bucket(Bucket="portrait-test")
        client.put_object(Bucket="portrait-test", Key="probe.txt", Body=b"ok")
        body = client.get_object(Bucket="portrait-test", Key="probe.txt")["Body"].read()
        assert body == b"ok"
